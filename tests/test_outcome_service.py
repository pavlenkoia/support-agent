from pathlib import Path

from app.core.db import Base, make_session_factory
from app.schemas.message import InboundMessage
from app.services.outcome import OutcomeService
from app.services.routing import RoutingService


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


class FakeDirectLLMService:
    def answer(self, text: str, kb_hits: list[dict]) -> dict:
        return {
            "direct_status": "insufficient_confidence",
            "response_text": f"Stub direct answer for: {text}",
            "used_kb_sources": [hit["source_ref"] for hit in kb_hits],
            "confidence": 0.4,
            "decision": "handoff",
            "reason": "test_stub",
        }


class FakeSummaryService:
    def summarize_case(self, messages: list[str]) -> str | None:
        cleaned = [message for message in messages if message]
        if not cleaned:
            return None
        return " | ".join(cleaned[:3])



def make_test_routing_service(tmp_path: Path) -> RoutingService:
    db_path = tmp_path / "test.db"
    session_factory = make_session_factory(f"sqlite+pysqlite:///{db_path}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])

    kb_root = tmp_path / "kb"
    (kb_root / "concepts").mkdir(parents=True)
    (kb_root / "index.md").write_text("# Wiki Index\n", encoding="utf-8")
    (kb_root / "concepts" / "hours.md").write_text(
        "# Business Hours\nWe are open from 9 to 18 on weekdays. Tandem jumps happen on weekends.\n",
        encoding="utf-8",
    )

    return RoutingService(
        session_factory=session_factory,
        knowledge_backend="filesystem",
        knowledge_root=str(kb_root),
        direct_llm=FakeDirectLLMService(),
        summary_service=FakeSummaryService(),
    )



def test_routing_service_delegates_outcome_execution_for_direct_route(tmp_path: Path) -> None:
    routing = make_test_routing_service(tmp_path)
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
        "decision": "answer",
        "reason": "sufficient_kb",
    }
    recorder = RecordingOutcomeService()
    routing.outcome = recorder

    result = routing.handle_inbound(payload)

    assert recorder.calls
    route, case, context, retrieval, user_message = recorder.calls[0]
    assert route["route"] == "direct_answer"
    assert isinstance(case["case_id"], int)
    assert context["user_message"] == "What are your business hours?"
    assert context["session_summary"] == "What are your business hours?"
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
        "session_summary": "Need help",
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
