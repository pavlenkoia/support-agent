from __future__ import annotations

from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.case import SupportCase
from app.schemas.message import InboundMessage
from app.services.audit import build_audit_event
from app.services.case_resolution import reset_conversation_session, resolve_case
from app.services.context_builder import build_context
from app.services.direct_llm import DirectLLMService
from app.services.kb_agent import KBAgentService
from app.services.orchestrator import OrchestratorService
from app.services.outcome import OutcomeService
from app.services.persistence import (
    persist_inbound_message,
    persist_outbound_message,
    persist_workflow_event,
)
from app.services.policy import PolicyService
from app.services.retrieval import RetrievalService
from app.services.tool_runtime import ToolRuntimeService
from app.workers.summarizer import SummaryService


class RoutingService:
    def __init__(
        self,
        session_factory=SessionLocal,
        knowledge_backend: str | None = None,
        knowledge_root: str | None = None,
        retrieval: RetrievalService | None = None,
        kb_agent: KBAgentService | None = None,
        direct_llm: DirectLLMService | None = None,
        outcome: OutcomeService | None = None,
        summary_service: SummaryService | None = None,
        policy: PolicyService | None = None,
        tool_runtime: ToolRuntimeService | None = None,
        orchestrator: OrchestratorService | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.knowledge_backend = knowledge_backend or settings.knowledge_backend
        self.knowledge_root = knowledge_root or settings.knowledge_root
        self.retrieval = retrieval or RetrievalService()
        self.kb_agent = kb_agent or KBAgentService()
        self.direct_llm = direct_llm or DirectLLMService()
        self.outcome = outcome or OutcomeService()
        self.summary_service = summary_service or SummaryService()
        self.policy = policy or PolicyService()
        self.tool_runtime = tool_runtime or ToolRuntimeService()
        self.orchestrator = orchestrator or OrchestratorService(
            retrieval=self.retrieval,
            kb_agent=self.kb_agent,
            direct_llm=self.direct_llm,
            policy=self.policy,
            tool_runtime=self.tool_runtime,
        )

    def reset_session(self, payload: InboundMessage) -> dict:
        with self.session_factory() as session:
            reset_result = reset_conversation_session(session, payload)
            persist_workflow_event(
                session,
                reset_result["case_id"],
                {
                    "command": "/new",
                    "conversation_id": reset_result["conversation_id"],
                    "new_case_id": reset_result["case_id"],
                    "closed_case_ids": reset_result["closed_case_ids"],
                    "closed_case_count": reset_result["closed_case_count"],
                    "channel": reset_result["channel"],
                    "external_user_id": reset_result["external_user_id"],
                    "external_chat_id": reset_result["external_chat_id"],
                },
                event_type="session_reset",
                actor="user:telegram_command",
            )
            session.commit()
            return reset_result

    def handle_inbound(self, payload: InboundMessage, *, persist_inbound: bool = True) -> dict:
        with self.session_factory() as session:
            case = resolve_case(session, payload)
            if persist_inbound:
                persist_inbound_message(session, case["case_id"], payload)
            context = build_context(session, payload, case, summary_service=self.summary_service)
            knowledge_query = self._build_knowledge_query(context)
            turn_classification = {
                "turn_type": "prompt_driven_dialogue",
                "confidence": 1.0,
                "reason": "external_system_prompt_with_tools",
            }
            orchestrated = self.orchestrator.run(
                text=payload.text,
                context=context,
                knowledge_backend=self.knowledge_backend,
                knowledge_root=self.knowledge_root,
                knowledge_query=knowledge_query,
            )
            retrieval = orchestrated["retrieval"]
            kb_result = orchestrated.get("kb_result", {})
            route = orchestrated["route"]
            response_strategy = orchestrated["response_strategy"]
            if orchestrated.get("loop_trace"):
                response_strategy["loop_trace"] = orchestrated["loop_trace"]

            persist_workflow_event(
                session,
                case["case_id"],
                turn_classification,
                event_type="turn_classified",
                actor="system:llm_turn_classifier",
            )

            outcome = self.outcome.execute(route, case, context, retrieval, payload.text)

            support_case = session.scalar(select(SupportCase).where(SupportCase.id == case["case_id"]))
            if support_case is not None:
                support_case.status = case["case_status"]
                support_case.route_mode = route["route"]
                session.flush()

            audit = build_audit_event(
                case,
                route,
                retrieval,
                outcome,
                turn_classification,
                response_strategy,
            )
            persist_workflow_event(
                session,
                case["case_id"],
                {
                    "response_strategy": response_strategy,
                    "kb_status": retrieval["kb_status"],
                    "kb_grounding_status": kb_result.get("grounding_status"),
                    "kb_mode": kb_result.get("kb_mode"),
                    "kb_skip_reason": retrieval.get("kb_skip_reason"),
                    "kb_snippet_count": len(retrieval.get("kb_snippets", [])),
                },
                event_type="response_strategy_selected",
                actor="system:routing",
            )
            persist_workflow_event(
                session,
                case["case_id"],
                audit,
                event_type="inbound_processed",
                actor="system:routing",
            )
            session.commit()

            return {
                "case": case,
                "context": context,
                "retrieval": retrieval,
                "kb_result": kb_result,
                "route": {
                    key: value
                    for key, value in route.items()
                    if key not in {"reply"}
                },
                "outcome": outcome,
                "audit": audit,
            }

    def record_outbound_message(self, case_id: int, text: str) -> None:
        cleaned = str(text or "").strip()
        if not cleaned:
            return

        with self.session_factory() as session:
            persist_outbound_message(session, case_id, cleaned)
            session.commit()

    def _build_knowledge_query(self, context: dict) -> str:
        current_message = str(context.get("user_message") or "").strip()
        recent_messages = context.get("recent_messages", [])
        if not isinstance(recent_messages, list):
            recent_messages = []
        recent_messages = [item for item in recent_messages if isinstance(item, dict)]
        if len(recent_messages) <= 1:
            return current_message

        previous_messages = recent_messages[:-1]
        summary = str(context.get("session_summary") or "").strip()
        parts = [f"Current user message: {current_message}"]
        if previous_messages:
            parts.append("Recent conversation context:")
            for item in previous_messages[-4:]:
                role = str(item.get("role") or "user").strip() or "user"
                content = str(item.get("content") or "").strip()
                if content:
                    parts.append(f"- {role}: {content}")
        if summary:
            parts.append(f"Session summary: {summary}")
        return "\n".join(parts)
