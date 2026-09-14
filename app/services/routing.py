from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.integrations.llm.factory import get_llm_client
from app.models.case import SupportCase
from app.models.message import Message
from app.models.workflow_event import WorkflowEvent
from app.schemas.message import InboundMessage
from app.services.audit import build_audit_event, text_sha256
from app.services.case_resolution import reset_conversation_session, resolve_case
from app.services.outcome import OutcomeService
from app.services.persistence import (
    persist_inbound_message,
    persist_outbound_message,
    persist_workflow_event,
)
from app.services.policy import PolicyService
from app.services.simple_answer_engine import SimpleAnswerEngine
from app.services.tool_runtime import ToolRuntimeService


class RoutingService:
    """Run the single supported customer-answer path."""

    def __init__(
        self,
        session_factory=SessionLocal,
        outcome: OutcomeService | None = None,
        policy: PolicyService | None = None,
        tool_runtime: ToolRuntimeService | None = None,
        simple_answer_engine: Any | None = None,
        answer_engine_mode: str | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.answer_engine_mode = answer_engine_mode or settings.answer_engine_mode
        if self.answer_engine_mode != "simple_full_corpus_natural":
            raise ValueError(f"unsupported answer engine: {self.answer_engine_mode}")
        self.outcome = outcome or OutcomeService()
        self.policy = policy or PolicyService()
        self.tool_runtime = tool_runtime or ToolRuntimeService()
        self.simple_answer_engine = simple_answer_engine or SimpleAnswerEngine(
            client=get_llm_client(
                provider=settings.direct_llm_provider,
                base_url=settings.direct_llm_base_url,
                api_key=settings.direct_llm_api_key,
                api_keys=settings.direct_llm_api_key_list,
                model=settings.direct_llm_model,
                timeout_seconds=settings.direct_llm_timeout_seconds,
                max_retries=settings.direct_llm_max_retries,
                retry_backoff_seconds=settings.direct_llm_retry_backoff_seconds,
                retry_deadline_seconds=settings.direct_llm_retry_deadline_seconds,
                drop_params=settings.openai_compatible_drop_params,
            ),
            profile_root=settings.support_agent_profile_root,
            max_corpus_chars=settings.simple_answer_max_corpus_chars,
            temperature=settings.direct_llm_temperature,
            preserve_grounded_text=True,
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
            return self._handle_inbound(session, case, payload)

    def _handle_inbound(self, session, case: dict, payload: InboundMessage) -> dict:
        messages = session.scalars(
            select(Message).where(Message.case_id == case["case_id"]).order_by(Message.id.desc()).limit(11)
        ).all()
        history = [
            {"role": message.role, "content": message.content}
            for message in reversed(messages)
            if message.role in {"user", "assistant"}
        ]
        if history and history[-1] == {"role": "user", "content": payload.text}:
            history.pop()
        context = {
            "user_message": payload.text,
            "recent_messages": history[-10:],
            "case_state": {
                "case_id": case["case_id"],
                "case_status": case["case_status"],
                "conversation_id": case["conversation_id"],
            },
        }
        tool_result = self.tool_runtime.collect(text=payload.text, kb_hits=[], conversation_context=context)
        engine_result = self.simple_answer_engine.answer(
            question=payload.text,
            history=context["recent_messages"],
            tool_observations=tool_result["tool_results"],
        )
        kind = str(engine_result["kind"])
        route_name = "answer" if kind in {"grounded_answer", "final_response", "social_reply"} else kind
        first_reply = not any(item["role"] == "assistant" for item in context["recent_messages"])
        raw_text = str(engine_result["response_text"] or "")
        response_text = "" if route_name == "retry_pending" else self.policy.finalize_simple_customer_text(
            raw_text, first_reply_in_dialogue=first_reply,
        )
        route = {
            "route": route_name,
            "reply": {"response_text": response_text},
            "reason": "simple_full_corpus_natural",
            "route_reason": "simple_full_corpus_natural",
            "route_confidence": 1.0,
            "answer_engine": self.answer_engine_mode,
            "outcome_kind": kind,
            "source_refs": engine_result["source_refs"],
        }
        retrieval = {"kb_status": "runtime_facts", "kb_snippets": [], "kb_skip_reason": "full_runtime_facts"}
        outcome = self.outcome.execute(route, case, context, retrieval, payload.text)
        support_case = session.scalar(select(SupportCase).where(SupportCase.id == case["case_id"]))
        if support_case is not None:
            support_case.status = case["case_status"]
            support_case.route_mode = route_name
        response_strategy = {"answer_engine": self.answer_engine_mode, **engine_result["telemetry"]}
        audit = build_audit_event(case, route, retrieval, outcome, {"turn_type": "simple_full_corpus"}, response_strategy)
        persist_workflow_event(session, case["case_id"], audit["response_strategy"], event_type="response_strategy_selected", actor="system:routing")
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
            message = persist_outbound_message(session, case_id, cleaned)
            digest = text_sha256(cleaned)
            delivered = select(WorkflowEvent.payload["trace_id"].as_string()).where(
                WorkflowEvent.case_id == case_id,
                WorkflowEvent.event_type == "answer_delivery_recorded",
                WorkflowEvent.payload["trace_id"].as_string().is_not(None),
            )
            candidates = session.scalars(select(WorkflowEvent).where(
                WorkflowEvent.case_id == case_id,
                WorkflowEvent.event_type == "inbound_processed",
                WorkflowEvent.payload["trace_packet"]["result"]["text_sha256"].as_string() == digest,
                WorkflowEvent.payload["trace_packet"]["trace_id"].as_string().not_in(delivered),
            ).limit(2)).all()
            trace_id = candidates[0].payload["trace_packet"]["trace_id"] if len(candidates) == 1 else None
            persist_workflow_event(session, case_id, {
                "trace_id": trace_id, "assistant_message_id": message.id,
                "text_sha256": digest, "status": "assistant_recorded",
                "correlation_status": "matched" if trace_id else ("ambiguous" if candidates else "unavailable"),
            }, event_type="answer_delivery_recorded", actor="system:routing")
            session.commit()
