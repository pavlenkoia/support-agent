import time
from pathlib import Path

from sqlalchemy import text

from app.services.telegram_gateway import TelegramGatewayService
from tests.test_inbound_message import make_test_routing_service


class RecordingSender:
    def __init__(self):
        self.calls = []

    def send_chat_action(self, chat_id: str, action: str = "typing") -> dict:
        self.calls.append(("action", chat_id, action))
        return {"ok": True, "sent": True, "action": action}

    def send_message(self, chat_id: str, text: str) -> dict:
        self.calls.append(("message", chat_id, text))
        return {"ok": True, "sent": True}


class StubRouting:
    def handle_inbound(self, payload) -> dict:
        return {
            "case": {"case_status": "resolved", "case_id": 1},
            "route": {"route": "cannot_answer"},
            "outcome": {
                "outcome_type": "cannot_answer",
                "outcome_status": "completed",
                "outcome_payload": {"response_text": "Сейчас не могу дать точный ответ на этот вопрос."},
            },
        }

    def record_outbound_message(self, case_id: int, text: str) -> None:
        self.recorded = (case_id, text)

    def reset_session(self, payload) -> dict:
        return {
            "conversation_id": 99,
            "case_id": 100,
            "closed_case_ids": [88],
            "closed_case_count": 1,
        }


def test_telegram_gateway_handles_new_command() -> None:
    sender = RecordingSender()
    service = TelegramGatewayService(routing=StubRouting(), sender=sender)

    result = service.handle_update({
        "update_id": 11,
        "message": {
            "message_id": 4,
            "text": "/new",
            "chat": {"id": 12345},
            "from": {"id": 777},
        },
    })

    assert result["ok"] is True
    assert result["ignored"] is False
    assert result["reply_text"] == "Сессию сбросил. Начинаем заново — можете отправить новый запрос."
    assert sender.calls == [
        ("message", "12345", "Сессию сбросил. Начинаем заново — можете отправить новый запрос."),
    ]


def test_telegram_gateway_sends_runtime_reply_and_persists_outbound_message(tmp_path: Path) -> None:
    sender = RecordingSender()
    routing = make_test_routing_service(tmp_path)
    routing.direct_llm.answer = lambda text, kb_hits, *, allow_general_without_kb=False, conversation_context=None: {
        "direct_status": "ready",
        "response_text": "Подготовка обязательна даже для первого прыжка.",
        "used_kb_sources": [hit["source_ref"] for hit in kb_hits],
        "confidence": 0.93,
        "decision": "answer",
        "reason": "grounded_answer",
    }
    service = TelegramGatewayService(routing=routing, sender=sender)

    first = service.handle_update({
        "update_id": 21,
        "message": {
            "message_id": 5,
            "text": "А без подготовки можно?",
            "chat": {"id": 12345},
            "from": {"id": 777},
        },
    })
    second = service.handle_update({
        "update_id": 22,
        "message": {
            "message_id": 6,
            "text": "Я офицер вдв",
            "chat": {"id": 12345},
            "from": {"id": 777},
        },
    })

    assert first["reply_text"] == "Подготовка обязательна даже для первого прыжка."
    assert second["reply_text"] == "Подготовка обязательна даже для первого прыжка."
    assert sender.calls[1] == ("message", "12345", "Подготовка обязательна даже для первого прыжка.")

    with routing.session_factory() as session:
        roles = session.execute(text("select role from messages order by id")).scalars().all()
        assert roles == ["user", "assistant", "user", "assistant"]
        contents = session.execute(text("select content from messages order by id")).scalars().all()
        assert contents[1] == "Подготовка обязательна даже для первого прыжка."
        assert contents[2] == "Я офицер вдв"


def test_telegram_gateway_typing_loop_keeps_single_action_for_fast_callback(tmp_path: Path) -> None:
    sender = RecordingSender()
    routing = make_test_routing_service(tmp_path)
    service = TelegramGatewayService(routing=routing, sender=sender, typing_interval_seconds=0.05)

    started = time.time()
    result = service.handle_update({
        "update_id": 31,
        "message": {
            "message_id": 7,
            "text": "привет",
            "chat": {"id": 12345},
            "from": {"id": 777},
        },
    })
    elapsed = time.time() - started

    assert result["typing"]["sent_actions"] >= 1
    assert elapsed < 1.5
