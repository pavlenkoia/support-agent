from app.services.outcome import OutcomeService
from app.services.routing import RoutingService
from app.schemas.message import InboundMessage


class RecordingOutcomeService:
    def __init__(self) -> None:
        self.calls: list[tuple[dict, dict, dict, dict, str]] = []

    def execute(
        self,
        route: dict,
        case: dict,
        context: dict,
        retrieval: dict,
        user_message: str,
    ) -> dict:
        self.calls.append((route, case.copy(), context.copy(), retrieval.copy(), user_message))
        case["case_status"] = "resolved"
        context["case_state"]["case_status"] = "resolved"
        return {
            "outcome_type": route["route"],
            "outcome_status": "completed",
            "outcome_payload": {"response_text": "delegated"},
        }


def test_routing_service_delegates_outcome_execution_for_direct_route() -> None:
    routing = RoutingService()
    payload = InboundMessage(
        channel="telegram",
        external_user_id="u3",
        external_chat_id="c3",
        text="What are your business hours?",
    )

    routing.direct_llm.answer = lambda text, kb_hits: {
        "direct_status": "ready",
        "response_text": "We are open from 9 to 18.",
        "used_kb_sources": [hit["source_ref"] for hit in kb_hits],
        "confidence": 0.95,
    }
    recorder = RecordingOutcomeService()
    routing.outcome = recorder

    result = routing.handle_inbound(payload)

    assert recorder.calls
    route, case, context, retrieval, user_message = recorder.calls[0]
    assert route["route"] == "direct_answer"
    assert case["case_id"] == "telegram:c3:u3"
    assert context["user_message"] == "What are your business hours?"
    assert retrieval["kb_status"] == "found"
    assert user_message == "What are your business hours?"
    assert result["outcome"]["outcome_payload"]["response_text"] == "delegated"


def test_outcome_service_builds_waiting_human_outcome() -> None:
    service = OutcomeService()
    case = {
        "case_id": "case-1",
        "conversation_id": "conv-1",
        "user_id": "user-1",
        "case_status": "open",
    }
    context = {
        "user_message": "Need help",
        "recent_turns": ["Need help"],
        "session_summary": None,
        "case_state": {"case_id": "case-1", "case_status": "open", "conversation_id": "conv-1"},
    }
    retrieval = {
        "kb_status": "found",
        "kb_snippets": [{"text": "x", "source_type": "kb_article", "source_ref": "kb://1"}],
    }
    route = {
        "route": "human_escalation",
        "route_reason": "low_confidence_and_hermes_disabled",
        "route_confidence": 0.4,
    }

    outcome = service.execute(route, case, context, retrieval, "Need help")

    assert outcome["outcome_type"] == "human_escalation"
    assert outcome["outcome_status"] == "waiting_human"
    assert outcome["outcome_payload"]["handoff_status"] == "queued"
    assert case["case_status"] == "waiting_human"
    assert context["case_state"]["case_status"] == "waiting_human"
