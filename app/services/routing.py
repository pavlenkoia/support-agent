from app.core.config import settings
from app.schemas.message import InboundMessage
from app.services.audit import build_audit_event
from app.services.case_resolution import resolve_case
from app.services.context_builder import build_context
from app.services.direct_llm import DirectLLMService
from app.services.outcome import OutcomeService
from app.services.retrieval import RetrievalService


class RoutingService:
    def __init__(self) -> None:
        self.retrieval = RetrievalService()
        self.direct_llm = DirectLLMService()
        self.outcome = OutcomeService()

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

    def handle_inbound(self, payload: InboundMessage) -> dict:
        case = resolve_case(payload)
        context = build_context(payload, case)
        retrieval = self.retrieval.retrieve(
            payload.text,
            settings.knowledge_backend,
            settings.knowledge_root,
        )
        direct = self.direct_llm.answer(payload.text, retrieval["kb_snippets"])
        route = self.decide_route(direct)
        outcome = self.outcome.execute(route, case, context, retrieval, payload.text)
        audit = build_audit_event(case, route, retrieval, outcome)
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
