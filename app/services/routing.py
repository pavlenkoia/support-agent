from __future__ import annotations

from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.case import SupportCase
from app.models.message import Message
from app.schemas.message import InboundMessage
from app.services.agent_tool_loop import AgentLoopService, DirectLLMActionAgent, WikiLookupTool
from app.services.audit import build_audit_event
from app.services.case_resolution import reset_conversation_session, resolve_case
from app.services.context_builder import build_context
from app.services.direct_llm import DirectLLMService
from app.services.kb_agent import KBAgentService
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
        agent_loop: AgentLoopService | None = None,
        answer_engine_mode: str | None = None,
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
        self.answer_engine_mode = answer_engine_mode or settings.answer_engine_mode
        if self.answer_engine_mode != "agent_tool_loop":
            raise ValueError(f"unsupported answer engine: {self.answer_engine_mode}")
        self.agent_loop = agent_loop or AgentLoopService(
            agent=DirectLLMActionAgent(self.direct_llm),
            wiki_lookup=WikiLookupTool(
                retrieval=self.retrieval,
                kb_agent=self.kb_agent,
                knowledge_backend=self.knowledge_backend,
                knowledge_root=self.knowledge_root,
            ),
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
            return self._handle_agent_tool_loop_inbound(session, case, payload)



    def _handle_agent_tool_loop_inbound(self, session, case: dict, payload: InboundMessage) -> dict:
        if self.agent_loop is None:
            raise RuntimeError("agent tool loop is not configured")
        context = build_context(session, payload, case, summary_service=self.summary_service)
        loop_result = self.agent_loop.run(text=payload.text, context=context)
        kb_result = loop_result.get("kb_result") if isinstance(loop_result.get("kb_result"), dict) else {}
        tool_observations = list(loop_result.get("tool_observations") or [])
        grounded = kb_result.get("grounding_status") == "ready"
        first_reply = not any(item.get("role") == "assistant" for item in context.get("recent_messages", []) if isinstance(item, dict))
        # Wiki is optional: every completed loop turn reaches the common finalizer.
        # An unavailable KB call is a technical observation, never a terminal customer outcome.
        policy_evidence = [] if grounded else self.policy.no_answer_policy_evidence()
        if policy_evidence:
            tool_observations.extend(
                {"kind": "profile_no_answer_option", "summary": item["text"], "source_ref": item["source_ref"]}
                for item in policy_evidence
            )
        final_result = self.direct_llm.respond(
            payload.text,
            kb_result,
            knowledge_mode="kb_grounded" if grounded or policy_evidence else "prompt_only",
            conversation_context=context,
            tool_observations=tool_observations,
            first_reply_in_dialogue=first_reply,
            response_intent="answer" if grounded else "missing_grounding",
        )
        final_route = str(final_result.get("route") or "cannot_answer")
        route_name = "answer" if final_route == "social_reply" else final_route
        outcome_kind = final_route
        if route_name == "retry_pending":
            response_text = ""
        else:
            response_text = self.policy.finalize_simple_customer_text(
                str(final_result.get("response_text") or ""),
                first_reply_in_dialogue=first_reply,
            )
        source_refs = [str(ref) for ref in kb_result.get("source_refs", []) if str(ref)] if grounded else []
        actions = list((loop_result.get("trace") or {}).get("actions") or [])
        tool_observations = list(loop_result.get("tool_observations") or [])
        wiki_observations = [
            item for item in tool_observations
            if isinstance(item, dict) and item.get("tool") == "wiki_lookup"
        ]
        wiki_status = str(wiki_observations[-1].get("status") or "not_found") if wiki_observations else "not_started"
        wiki_used = bool(wiki_observations)
        llm_trace = [item for item in loop_result.get("llm_trace", []) if isinstance(item, dict)]
        llm_trace.extend(item for item in final_result.get("llm_trace", []) if isinstance(item, dict))
        retrieval = {
            "kb_status": wiki_status,
            "kb_snippets": [],
            "kb_skip_reason": "agent_tool_loop_no_wiki_needed" if not wiki_used else None,
        }
        route = {
            "route": route_name,
            "reply": {"response_text": response_text},
            "reason": outcome_kind,
            "route_reason": outcome_kind,
            "route_confidence": 1.0,
            "answer_engine": "agent_tool_loop",
            "outcome_kind": outcome_kind,
            "source_refs": source_refs,
        }
        outcome = self.outcome.execute(route, case, context, retrieval, payload.text)
        support_case = session.scalar(select(SupportCase).where(SupportCase.id == case["case_id"]))
        if support_case is not None:
            support_case.status = case["case_status"]
            support_case.route_mode = route_name
        response_strategy = {
            "answer_engine": "agent_tool_loop",
            "agent_actions": actions,
            "tool_observations": tool_observations,
            "wiki_used": wiki_used,
            "llm_trace": llm_trace,
            "logical_llm_call_count": len(llm_trace),
            "provider_attempt_count": sum(int(item.get("attempts") or 1) for item in llm_trace),
        }
        audit = build_audit_event(case, route, retrieval, outcome, {"turn_type": "agent_tool_loop"}, response_strategy)
        audit["agent_actions"] = actions
        persist_workflow_event(session, case["case_id"], response_strategy, event_type="response_strategy_selected", actor="system:routing")
        persist_workflow_event(session, case["case_id"], audit, event_type="inbound_processed", actor="system:routing")
        session.commit()
        return {
            "case": case,
            "context": context,
            "retrieval": retrieval,
            "kb_result": {},
            "route": {key: value for key, value in route.items() if key != "reply"},
            "outcome": outcome,
            "audit": audit,
        }

    def persist_inbound_message(self, case_id: int, payload: InboundMessage) -> None:
        with self.session_factory() as session:
            persist_inbound_message(session, case_id, payload)
            session.commit()

    def record_outbound_message(self, case_id: int, text: str) -> None:
        cleaned = str(text or "").strip()
        if not cleaned:
            return

        with self.session_factory() as session:
            persist_outbound_message(session, case_id, cleaned)
            session.commit()
