from app.core.config import settings
from app.schemas.message import InboundMessage
from app.services.audit import build_audit_event
from app.services.context_builder import build_context
from app.services.direct_llm import DirectLLMService
from app.services.escalation import EscalationService
from app.services.hermes_backend import HermesBackendService
from app.services.retrieval import RetrievalService


class RoutingService:
    def __init__(self) -> None:
        self.retrieval = RetrievalService()
        self.direct_llm = DirectLLMService()
        self.hermes = HermesBackendService()
        self.escalation = EscalationService()

    def handle_inbound(self, payload: InboundMessage) -> dict:
        context = build_context(payload)
        kb_hits = self.retrieval.retrieve(
            payload.text,
            settings.knowledge_backend,
            settings.knowledge_root,
        )
        direct = self.direct_llm.answer(payload.text, kb_hits)

        if direct["confidence"] >= 0.7:
            return {**direct, "audit": build_audit_event("direct_answer", "confidence_threshold_met")}

        if settings.hermes_backend_enabled:
            hermes_result = self.hermes.analyze(payload.text, context, kb_hits)
            return {**hermes_result, "audit": build_audit_event("hermes_escalation", "low_confidence_direct_path")}

        human = self.escalation.to_human("low_confidence_and_hermes_disabled")
        return {
            **human,
            "kb_hits": kb_hits,
            "context": context,
            "audit": build_audit_event("human_escalation", "low_confidence_and_hermes_disabled"),
        }
