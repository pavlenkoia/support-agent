from fastapi.testclient import TestClient

from app.main import app
from app.schemas.message import InboundMessage
from app.services.routing import RoutingService


client = TestClient(app)


def test_inbound_message_returns_case_driven_human_fallback_payload() -> None:
    response = client.post('/api/v1/messages/inbound', json={
        "channel": "telegram",
        "external_user_id": "u1",
        "external_chat_id": "c1",
        "text": "Where is my order?",
    })

    assert response.status_code == 200
    payload = response.json()

    assert payload["case"]["case_status"] == "waiting_human"
    assert payload["case"]["user_id"] == "telegram:u1"
    assert payload["case"]["channel_account_id"] == "telegram:c1"
    assert payload["route"]["route"] == "human_escalation"
    assert payload["route"]["route_reason"] == "low_confidence_and_hermes_disabled"
    assert payload["outcome"]["outcome_type"] == "human_escalation"
    assert payload["outcome"]["outcome_status"] == "waiting_human"
    assert payload["audit"]["route"] == "human_escalation"
    assert payload["audit"]["outcome_status"] == "waiting_human"
    assert payload["retrieval"]["kb_status"] == "found"
    assert payload["context"]["user_message"] == "Where is my order?"


def test_routing_service_returns_direct_answer_contract_when_confidence_is_high() -> None:
    routing = RoutingService()
    payload = InboundMessage(
        channel="telegram",
        external_user_id="u2",
        external_chat_id="c2",
        text="What are your business hours?",
    )

    routing.direct_llm.answer = lambda text, kb_hits: {
        "direct_status": "ready",
        "response_text": "We are open from 9 to 18.",
        "used_kb_sources": [hit["source_ref"] for hit in kb_hits],
        "confidence": 0.91,
    }

    result = routing.handle_inbound(payload)

    assert result["route"]["route"] == "direct_answer"
    assert result["route"]["route_confidence"] == 0.91
    assert result["outcome"]["outcome_type"] == "direct_answer"
    assert result["outcome"]["outcome_status"] == "completed"
    assert result["outcome"]["outcome_payload"]["response_text"] == "We are open from 9 to 18."
    assert result["audit"]["route_reason"] == "confidence_threshold_met"
