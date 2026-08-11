import time
from datetime import UTC, datetime, timedelta
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


def test_telegram_gateway_queues_inbound_without_blocking_or_sending() -> None:
    sender = RecordingSender()
    routing = StubRouting()
    service = TelegramGatewayService(routing=routing, sender=sender)

    result = service.handle_update({
        "update_id": 12,
        "message": {
            "message_id": 8,
            "text": "Короткая реплика",
            "chat": {"id": 12345},
            "from": {"id": 777},
        },
    })

    assert result["queued"] is True
    assert sender.calls == []


def test_telegram_gateway_coalesces_two_messages_into_one_runtime_turn() -> None:
    class CoalescingRouting(StubRouting):
        def __init__(self) -> None:
            self.payloads = []
            self.persisted = []

        def handle_inbound(self, payload, *, persist_inbound=True) -> dict:
            assert persist_inbound is False
            self.payloads.append(payload)
            return super().handle_inbound(payload)

        def persist_inbound_message(self, case_id: int, payload) -> None:
            self.persisted.append((case_id, payload.text))

    sender = RecordingSender()
    routing = CoalescingRouting()
    service = TelegramGatewayService(routing=routing, sender=sender)
    for message_id, text in [(1, "Здравствуйте"), (2, "Сколько стоит прыжок с самолёта?")]:
        service.handle_update({"update_id": message_id, "message": {"message_id": message_id, "text": text, "chat": {"id": 12345}, "from": {"id": 777}}})

    service.queue.flush_due(now=datetime.now(UTC) + timedelta(seconds=6), background=False)

    assert [payload.text for payload in routing.payloads] == ["Здравствуйте\nСколько стоит прыжок с самолёта?"]
    assert routing.persisted == [(1, "Здравствуйте\nСколько стоит прыжок с самолёта?")]
    assert [call for call in sender.calls if call[0] == "message"] == [("message", "12345", "Сейчас не могу дать точный ответ на этот вопрос.")]


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


def test_telegram_gateway_persists_one_combined_user_and_assistant_message(tmp_path: Path) -> None:
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

    for update_id, message_id, text_value in [
        (21, 5, "А без подготовки можно?"),
        (22, 6, "Я офицер вдв"),
    ]:
        result = service.handle_update({
            "update_id": update_id,
            "message": {"message_id": message_id, "text": text_value, "chat": {"id": 12345}, "from": {"id": 777}},
        })
        assert result["queued"] is True

    service.queue.flush_due(now=datetime.now(UTC) + timedelta(seconds=6), background=False)

    assert sender.calls == [("message", "12345", "Здравствуйте! Подготовка обязательна даже для первого прыжка.")]
    with routing.session_factory() as session:
        roles = session.execute(text("select role from messages order by id")).scalars().all()
        assert roles == ["user", "assistant"]
        contents = session.execute(text("select content from messages order by id")).scalars().all()
        assert contents == [
            "А без подготовки можно?\nЯ офицер вдв",
            "Здравствуйте! Подготовка обязательна даже для первого прыжка.",
        ]


def test_telegram_gateway_polling_handler_returns_without_typing_or_delivery(tmp_path: Path) -> None:
    sender = RecordingSender()
    routing = make_test_routing_service(tmp_path)
    service = TelegramGatewayService(routing=routing, sender=sender, typing_interval_seconds=0.05)

    started = time.time()
    result = service.handle_update({
        "update_id": 31,
        "message": {"message_id": 7, "text": "привет", "chat": {"id": 12345}, "from": {"id": 777}},
    })

    assert result["queued"] is True
    assert time.time() - started < 0.2
    assert sender.calls == []
