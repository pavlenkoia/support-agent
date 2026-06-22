from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_inbound_message_falls_back_to_human_when_hermes_disabled() -> None:
    response = client.post('/api/v1/messages/inbound', json={
        "channel": "telegram",
        "external_user_id": "u1",
        "external_chat_id": "c1",
        "text": "Where is my order?",
    })

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "human_escalation"
    assert payload["audit"]["mode"] == "human_escalation"
    assert payload["kb_hits"]
