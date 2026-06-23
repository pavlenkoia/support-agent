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
from app.services.outcome import OutcomeService
from app.services.persistence import persist_inbound_message, persist_workflow_event
from app.services.retrieval import RetrievalService
from app.workers.summarizer import SummaryService


class RoutingService:
    def __init__(
        self,
        session_factory=SessionLocal,
        knowledge_backend: str | None = None,
        knowledge_root: str | None = None,
        retrieval: RetrievalService | None = None,
        direct_llm: DirectLLMService | None = None,
        outcome: OutcomeService | None = None,
        summary_service: SummaryService | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.knowledge_backend = knowledge_backend or settings.knowledge_backend
        self.knowledge_root = knowledge_root or settings.knowledge_root
        self.retrieval = retrieval or RetrievalService()
        self.direct_llm = direct_llm or DirectLLMService()
        self.outcome = outcome or OutcomeService()
        self.summary_service = summary_service or SummaryService()

    def decide_route(self, direct: dict) -> dict:
        if direct["confidence"] >= 0.7:
            return {
                "route": "direct_answer",
                "route_reason": "confidence_threshold_met",
                "route_confidence": direct["confidence"],
                "direct_result": direct,
            }

        if settings.hermes_backend_enabled:
            return {
                "route": "hermes_escalation",
                "route_reason": "low_confidence_direct_path",
                "route_confidence": direct["confidence"],
            }

        return {
            "route": "human_escalation",
            "route_reason": "low_confidence_and_hermes_disabled",
            "route_confidence": direct["confidence"],
        }

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

    def handle_inbound(self, payload: InboundMessage) -> dict:
        with self.session_factory() as session:
            case = resolve_case(session, payload)
            persist_inbound_message(session, case["case_id"], payload)
            context = build_context(session, payload, case, summary_service=self.summary_service)

            turn_classification = self.direct_llm.classify_turn(payload.text)
            if turn_classification["turn_type"] == "social_turn":
                retrieval = {
                    "kb_status": "skipped_social_turn",
                    "kb_skip_reason": "model_classified_social_turn",
                    "kb_snippets": [],
                }
                direct = self.direct_llm.answer(payload.text, [], allow_general_without_kb=True)
                response_strategy = {
                    "classifier_path": "llm_turn_classifier",
                    "response_path": "social_responder_without_kb",
                    "knowledge_path": "kb_skipped",
                }
            else:
                retrieval = self.retrieval.retrieve(
                    payload.text,
                    self.knowledge_backend,
                    self.knowledge_root,
                )
                direct = self.direct_llm.answer(payload.text, retrieval["kb_snippets"])
                response_strategy = {
                    "classifier_path": "llm_turn_classifier",
                    "response_path": "kb_grounded_direct_llm",
                    "knowledge_path": "kb_retrieval",
                }

            persist_workflow_event(
                session,
                case["case_id"],
                turn_classification,
                event_type="turn_classified",
                actor="system:llm_turn_classifier",
            )

            route = self.decide_route(direct)
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
                "route": {
                    key: value
                    for key, value in route.items()
                    if key not in {"direct_result"}
                },
                "outcome": outcome,
                "audit": audit,
            }
