from pathlib import Path
import json

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.deps import get_routing_service
from app.core.db import Base, make_session_factory
from app.main import app
from app.schemas.message import InboundMessage
from app.services.routing import RoutingService


client = TestClient(app)


class FakeDirectLLMService:
    def classify_turn(self, text: str) -> dict:
        return {
            "turn_type": "knowledge_request",
            "confidence": 0.0,
            "reason": "test_default",
        }

    def answer(self, text: str, kb_hits: list[dict], *, allow_general_without_kb: bool = False) -> dict:
        return {
            "direct_status": "insufficient_confidence",
            "response_text": f"Stub direct answer for: {text}",
            "used_kb_sources": [hit["source_ref"] for hit in kb_hits],
            "confidence": 0.4,
            "decision": "handoff",
            "reason": "test_stub",
            "allow_general_without_kb": allow_general_without_kb,
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

    routing = RoutingService(
        session_factory=session_factory,
        knowledge_backend="filesystem",
        knowledge_root=str(kb_root),
        direct_llm=FakeDirectLLMService(),
        summary_service=FakeSummaryService(),
    )
    return routing



def test_inbound_message_returns_db_backed_human_fallback_payload(tmp_path: Path) -> None:
    routing = make_test_routing_service(tmp_path)
    app.dependency_overrides[get_routing_service] = lambda: routing
    try:
        response = client.post('/api/v1/messages/inbound', json={
            "channel": "telegram",
            "external_user_id": "u1",
            "external_chat_id": "c1",
            "text": "What are your business hours?",
        })
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()

    assert payload["case"]["case_status"] == "waiting_human"
    assert isinstance(payload["case"]["user_id"], int)
    assert isinstance(payload["case"]["channel_account_id"], int)
    assert isinstance(payload["case"]["conversation_id"], int)
    assert isinstance(payload["case"]["case_id"], int)
    assert payload["route"]["route"] == "human_escalation"
    assert payload["route"]["route_reason"] == "low_confidence_and_hermes_disabled"
    assert payload["outcome"]["outcome_type"] == "human_escalation"
    assert payload["outcome"]["outcome_status"] == "waiting_human"
    assert payload["audit"]["route"] == "human_escalation"
    assert payload["audit"]["outcome_status"] == "waiting_human"
    assert payload["audit"]["turn_classifier"]["turn_type"] == "knowledge_request"
    assert payload["audit"]["response_strategy"]["response_path"] == "kb_grounded_direct_llm"
    assert payload["retrieval"]["kb_status"] == "found"
    assert payload["context"]["user_message"] == "What are your business hours?"
    assert payload["context"]["recent_turns"] == ["What are your business hours?"]
    assert payload["context"]["session_summary"] == "What are your business hours?"



def test_routing_service_persists_entities_and_reuses_open_case(tmp_path: Path) -> None:
    routing = make_test_routing_service(tmp_path)

    first = routing.handle_inbound(InboundMessage(
        channel="telegram",
        external_user_id="u2",
        external_chat_id="c2",
        text="Первое сообщение",
    ))
    second = routing.handle_inbound(InboundMessage(
        channel="telegram",
        external_user_id="u2",
        external_chat_id="c2",
        text="Второе сообщение",
    ))

    assert second["case"]["user_id"] == first["case"]["user_id"]
    assert second["case"]["channel_account_id"] == first["case"]["channel_account_id"]
    assert second["case"]["conversation_id"] == first["case"]["conversation_id"]
    assert second["case"]["case_id"] == first["case"]["case_id"]
    assert second["case"]["case_status"] == "waiting_human"
    assert second["context"]["recent_turns"] == ["Первое сообщение", "Второе сообщение"]
    assert second["context"]["session_summary"] == "Первое сообщение | Второе сообщение"

    with routing.session_factory() as session:
        assert session.execute(text("select count(*) from users")).scalar_one() == 1
        assert session.execute(text("select count(*) from channel_accounts")).scalar_one() == 1
        assert session.execute(text("select count(*) from conversations")).scalar_one() == 1
        assert session.execute(text("select count(*) from support_cases")).scalar_one() == 1
        assert session.execute(text("select count(*) from messages")).scalar_one() == 2
        assert session.execute(text("select count(*) from workflow_events")).scalar_one() == 6
        event_types = session.execute(text("select event_type from workflow_events order by id")).scalars().all()
        assert event_types == [
            "turn_classified",
            "response_strategy_selected",
            "inbound_processed",
            "turn_classified",
            "response_strategy_selected",
            "inbound_processed",
        ]
        payload = session.execute(text("select payload from workflow_events order by id desc limit 1")).scalar_one()
        if isinstance(payload, str):
            payload = json.loads(payload)
        assert payload["turn_classifier"]["turn_type"] == "knowledge_request"
        assert payload["response_strategy"]["classifier_path"] == "llm_turn_classifier"
        assert payload["response_strategy"]["knowledge_path"] == "kb_retrieval"



def test_routing_service_returns_direct_answer_contract_when_confidence_is_high(tmp_path: Path) -> None:
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
        "confidence": 0.91,
        "decision": "answer",
        "reason": "sufficient_kb",
    }

    result = routing.handle_inbound(payload)

    assert result["route"]["route"] == "direct_answer"
    assert result["route"]["route_confidence"] == 0.91
    assert result["outcome"]["outcome_type"] == "direct_answer"
    assert result["outcome"]["outcome_status"] == "completed"
    assert result["outcome"]["outcome_payload"]["response_text"] == "We are open from 9 to 18."
    assert result["audit"]["route_reason"] == "confidence_threshold_met"



def test_routing_service_uses_model_for_social_turn_without_kb(tmp_path: Path) -> None:
    routing = make_test_routing_service(tmp_path)
    payload = InboundMessage(
        channel="telegram",
        external_user_id="u4",
        external_chat_id="c4",
        text="привет",
    )

    calls: list[dict] = []

    routing.direct_llm.classify_turn = lambda text: {
        "turn_type": "social_turn",
        "confidence": 0.98,
        "reason": "model_classified_social_turn",
    }

    def social_answer(text, kb_hits, *, allow_general_without_kb=False):
        calls.append({
            "text": text,
            "kb_hits": kb_hits,
            "allow_general_without_kb": allow_general_without_kb,
        })
        return {
            "direct_status": "ready",
            "response_text": "Рад вас слышать. Задайте вопрос, и я постараюсь помочь.",
            "used_kb_sources": [],
            "confidence": 0.82,
            "decision": "answer",
            "reason": "social_llm",
        }

    routing.direct_llm.answer = social_answer

    result = routing.handle_inbound(payload)

    assert calls == [{
        "text": "привет",
        "kb_hits": [],
        "allow_general_without_kb": True,
    }]
    assert result["route"]["route"] == "direct_answer"
    assert result["route"]["route_confidence"] == 0.82
    assert result["outcome"]["outcome_type"] == "direct_answer"
    assert result["outcome"]["outcome_status"] == "completed"
    assert result["outcome"]["outcome_payload"]["reason"] == "social_llm"
    assert result["outcome"]["outcome_payload"]["response_text"] == "Рад вас слышать. Задайте вопрос, и я постараюсь помочь."
    assert result["retrieval"]["kb_status"] == "skipped_social_turn"
    assert result["retrieval"]["kb_skip_reason"] == "model_classified_social_turn"
    assert result["audit"]["turn_classifier"]["turn_type"] == "social_turn"
    assert result["audit"]["response_strategy"]["response_path"] == "social_responder_without_kb"
    assert result["audit"]["response_strategy"]["classifier_path"] == "llm_turn_classifier"


def test_routing_service_reset_session_closes_old_case_and_prepares_new_one(tmp_path: Path) -> None:
    routing = make_test_routing_service(tmp_path)
    first = routing.handle_inbound(InboundMessage(
        channel="telegram",
        external_user_id="u5",
        external_chat_id="c5",
        text="Нужна помощь",
    ))

    reset = routing.reset_session(InboundMessage(
        channel="telegram",
        external_user_id="u5",
        external_chat_id="c5",
        text="/new",
    ))

    second = routing.handle_inbound(InboundMessage(
        channel="telegram",
        external_user_id="u5",
        external_chat_id="c5",
        text="Начинаем заново",
    ))

    assert reset["closed_case_count"] == 1
    assert reset["closed_case_ids"] == [first["case"]["case_id"]]
    assert second["case"]["case_id"] == reset["case_id"]
    assert second["case"]["case_id"] != first["case"]["case_id"]

    with routing.session_factory() as session:
        statuses = session.execute(text("select id, status, route_mode from support_cases order by id")).all()
        assert statuses == [
            (first["case"]["case_id"], "resolved", "session_reset"),
            (reset["case_id"], "waiting_human", "human_escalation"),
        ]
        reset_event = session.execute(text("select event_type, payload from workflow_events where event_type = 'session_reset' order by id desc limit 1")).one()
        payload = reset_event[1]
        if isinstance(payload, str):
            payload = json.loads(payload)
        assert reset_event[0] == "session_reset"
        assert payload["command"] == "/new"
        assert payload["new_case_id"] == reset["case_id"]
