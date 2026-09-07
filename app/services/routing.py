from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.case import SupportCase
from app.models.workflow_event import WorkflowEvent
from app.schemas.message import InboundMessage
from app.services.agent_tool_loop import (
    CalendarLookupTool,
    UnifiedTurnService,
    WikiLookupTool,
)
from app.services.audit import (
    build_audit_event,
    clean_llm_trace,
    text_sha256,
    trace_metrics,
)
from app.services.case_resolution import reset_conversation_session, resolve_case
from app.services.context_builder import build_context
from app.services.direct_llm import DirectLLMService
from app.services.final_response_validation import validate_final_response
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
        turn_service: UnifiedTurnService | None = None,
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
        self.turn_service = turn_service or UnifiedTurnService(
            model=self.direct_llm,
            calendar_lookup=CalendarLookupTool(runtime=self.tool_runtime),
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
        context = build_context(session, payload, case, summary_service=self.summary_service)
        loop_result = self.turn_service.run(text=payload.text, context=context)
        raw_kb_result = loop_result.get("kb_result")
        kb_result: dict[str, Any] = raw_kb_result if isinstance(raw_kb_result, dict) else {}
        raw_final_result = loop_result.get("final_result")
        finalization_requested = loop_result.get("finalization_requested")
        tool_observations = [item for item in loop_result.get("tool_observations", []) if isinstance(item, dict)]
        calendar_ready = any(item.get("tool") == "calendar_lookup" and item.get("status") == "ready" for item in tool_observations)
        technical_statuses = {"llm_unavailable", "retry_pending", "unavailable"}
        technical_kb_failure = kb_result.get("grounding_status") in technical_statuses or any(
            item.get("status") in technical_statuses for item in tool_observations
        )
        grounded = kb_result.get("grounding_status") == "ready"
        finalization_has_evidence = grounded or calendar_ready
        first_reply = not any(item.get("role") == "assistant" for item in context.get("recent_messages", []) if isinstance(item, dict))
        finalizer_invoked = False
        technical_result = {"route": "retry_pending", "response_text": "", "confidence": None, "reason": "invalid_selector_output"}
        if raw_final_result is not None:
            # Selector boundaries may only propagate textless technical failures.
            final_result = raw_final_result if isinstance(raw_final_result, dict) and raw_final_result.get("route") == "retry_pending" else technical_result
        elif technical_kb_failure:
            final_result = {**technical_result, "reason": "kb_technical_failure"}
        elif not isinstance(finalization_requested, dict) or finalization_requested.get("response_intent") not in {"answer", "social_reply", "clarification", "missing_grounding"}:
            final_result = technical_result
        else:
            response_intent = finalization_requested["response_intent"]
            policy_evidence = [] if finalization_has_evidence or response_intent == "social_reply" else self.policy.no_answer_policy_evidence()
            finalization_observations = list(tool_observations)
            finalization_observations.extend(
                {"kind": "profile_no_answer_option", "summary": item["text"], "source_ref": item["source_ref"]}
                for item in policy_evidence
            )
            # Keep calendar facts separate from business evidence, and exclude
            # navigation results/raw pages before the common writer boundary.
            finalization_kb_result = {
                "grounding_status": str(kb_result.get("grounding_status") or "not_found"),
                "answer_basis": kb_result.get("answer_basis", "") if grounded else "",
                "grounded_facts": kb_result.get("grounded_facts", []) if grounded else [],
                "source_refs": kb_result.get("source_refs", []) if grounded else [],
            }
            finalizer_invoked = True
            final_result = self.direct_llm.respond(
                payload.text,
                finalization_kb_result,
                knowledge_mode="kb_grounded" if finalization_has_evidence or policy_evidence else "prompt_only",
                conversation_context=context,
                tool_observations=finalization_observations,
                first_reply_in_dialogue=first_reply,
                response_intent=response_intent,
            )
        final_result = {**final_result, **validate_final_response(final_result, allow_technical=True)}
        if final_result["route"] != "retry_pending":
            formatted = self.policy.finalize_simple_customer_text(
                final_result["response_text"], first_reply_in_dialogue=first_reply,
            )
            final_result = {
                **final_result,
                **validate_final_response({**final_result, "response_text": formatted}, channel=payload.channel),
            }
        final_route = final_result["route"]
        route_name = "answer" if final_route == "social_reply" else final_route
        outcome_kind = final_route
        response_text = final_result["response_text"]
        source_refs = [str(ref) for ref in kb_result.get("source_refs", []) if str(ref)] if grounded else []
        actions = list((loop_result.get("trace") or {}).get("actions") or [])
        if finalizer_invoked:
            actions.append("final_response")
        tool_observations = list(loop_result.get("tool_observations") or [])
        wiki_observations = [
            item for item in tool_observations
            if isinstance(item, dict) and item.get("tool") == "wiki_lookup"
        ]
        wiki_status = str(wiki_observations[-1].get("status") or "not_found") if wiki_observations else "not_started"
        wiki_used = bool(wiki_observations)
        llm_trace = [item for item in loop_result.get("llm_trace", []) if isinstance(item, dict)]
        finalization_llm_trace = [item for item in final_result.get("llm_trace", []) if isinstance(item, dict)]
        llm_trace.extend(finalization_llm_trace)
        retrieval = {
            "kb_status": wiki_status,
            "kb_snippets": [],
            "kb_skip_reason": "agent_tool_loop_no_wiki_needed" if not wiki_used else None,
        }
        route = {
            "route": route_name,
            "reply": {"response_text": response_text},
            "reason": final_result["reason"],
            "route_reason": final_result["reason"],
            "route_confidence": final_result["confidence"],
            "answer_engine": "agent_tool_loop",
            "outcome_kind": outcome_kind,
            "source_refs": source_refs,
        }
        outcome = self.outcome.execute(route, case, context, retrieval, payload.text)
        outcome["outcome_reason"] = final_result["reason"]
        support_case = session.scalar(select(SupportCase).where(SupportCase.id == case["case_id"]))
        if support_case is not None:
            support_case.status = case["case_status"]
            support_case.route_mode = route_name
        llm_trace = clean_llm_trace(llm_trace)
        response_strategy = {
            "answer_engine": "agent_tool_loop",
            "agent_actions": actions,
            "tool_observations": tool_observations,
            "tool_requests": list(loop_result.get("tool_requests") or []),
            "source_turn": {"channel": payload.channel, "external_message_id": payload.external_message_id, "external_event_id": payload.external_event_id},
            "wiki_used": wiki_used,
            "finalizer_invoked": finalizer_invoked,
            "llm_trace": llm_trace,
            "finalization_llm_trace": clean_llm_trace(finalization_llm_trace),
            **trace_metrics(llm_trace),
        }
        audit = build_audit_event(case, route, retrieval, outcome, {"turn_type": "agent_tool_loop"}, response_strategy)
        audit["agent_actions"] = actions
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
