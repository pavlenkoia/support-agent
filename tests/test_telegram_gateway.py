import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from app.models.transport_event import TransportEvent
from app.schemas.message import InboundMessage
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
    def handle_inbound(self, payload, *, persist_inbound=True) -> dict:
        return {
            "case": {"case_status": "resolved", "case_id": 1},
            "route": {"route": "cannot_answer"},
            "outcome": {
                "outcome_type": "cannot_answer",
                "outcome_status": "completed",
                "outcome_payload": {"response_text": "Сейчас не могу дать точный ответ на этот вопрос."},
            },
        }

    def persist_inbound_message(self, case_id: int, payload) -> None:
        self.persisted_inbound = (case_id, payload.text)

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
    for message_id, text_value in [(1, "Здравствуйте"), (2, "Сколько стоит прыжок с самолёта?")]:
        service.handle_update({"update_id": message_id, "message": {"message_id": message_id, "text": text_value, "chat": {"id": 12345}, "from": {"id": 777}}})

    service.queue.flush_due(now=datetime.now(UTC) + timedelta(seconds=6), background=False)

    assert [payload.text for payload in routing.payloads] == ["Здравствуйте\nСколько стоит прыжок с самолёта?"]
    assert routing.persisted == [(1, "Здравствуйте\nСколько стоит прыжок с самолёта?")]
    assert [call for call in sender.calls if call[0] == "message"] == [("message", "12345", "Сейчас не могу дать точный ответ на этот вопрос.")]


def test_telegram_gateway_sends_typing_before_queued_reply() -> None:
    sender = RecordingSender()
    service = TelegramGatewayService(routing=StubRouting(), sender=sender)

    service.handle_update({
        "update_id": 13,
        "message": {"message_id": 9, "text": "Подскажите стоимость", "chat": {"id": 12345}, "from": {"id": 777}},
    })
    service.queue.flush_due(now=datetime.now(UTC) + timedelta(seconds=6), background=False)

    assert sender.calls == [
        ("action", "12345", "typing"),
        ("message", "12345", "Сейчас не могу дать точный ответ на этот вопрос."),
    ]


def test_telegram_gateway_keeps_typing_during_queued_generation() -> None:
    class SlowRouting(StubRouting):
        def handle_inbound(self, payload, *, persist_inbound=True) -> dict:
            time.sleep(0.12)
            return super().handle_inbound(payload, persist_inbound=persist_inbound)

    sender = RecordingSender()
    service = TelegramGatewayService(routing=SlowRouting(), sender=sender, typing_interval_seconds=0.02)
    service.handle_update({
        "update_id": 14,
        "message": {"message_id": 10, "text": "Подскажите стоимость", "chat": {"id": 12345}, "from": {"id": 777}},
    })
    service.queue.flush_due(now=datetime.now(UTC) + timedelta(seconds=6), background=False)

    assert len([call for call in sender.calls if call[0] == "action"]) >= 2
    assert sender.calls[-1] == ("message", "12345", "Сейчас не могу дать точный ответ на этот вопрос.")


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
    routing.answer_engine_mode = "simple_full_corpus"
    routing.simple_answer_engine = type(
        "Engine",
        (),
        {"answer": lambda self, **kwargs: {"kind": "grounded_answer", "response_text": "Подготовка обязательна даже для первого прыжка.", "source_refs": ["compiled/concepts/pricing.md"], "telemetry": {"answer_engine": "simple_full_corpus", "logical_llm_call_count": 1, "provider_attempt_count": 1}}},
    )()
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

    assert sender.calls == [
        ("action", "12345", "typing"),
        ("message", "12345", "Здравствуйте! Подготовка обязательна даже для первого прыжка."),
    ]
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


class RetryFailureSender(RecordingSender):
    def send_message(self, chat_id: str, text: str) -> dict:
        self.calls.append(("message", chat_id, text))
        return {"ok": False, "sent": False, "error": "temporary_failure"}


class RetryRouting:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory
        self.calls = 0

    def handle_inbound(self, payload, *, persist_inbound=True) -> dict:
        self.calls += 1
        return {
            "case": {"case_status": "open", "case_id": 1},
            "route": {"route": "retry_pending"},
            "outcome": {
                "outcome_type": "retry_pending",
                "outcome_status": "queued",
                "outcome_payload": {"response_text": ""},
            },
        }

    def persist_inbound_message(self, case_id: int, payload) -> None:
        from app.services.persistence import persist_inbound_message

        with self.session_factory() as session:
            persist_inbound_message(session, case_id, payload)
            session.commit()

    def record_outbound_message(self, case_id: int, text: str) -> None:
        from app.services.persistence import persist_outbound_message

        with self.session_factory() as session:
            persist_outbound_message(session, case_id, text)
            session.commit()


def test_telegram_gateway_provider_failure_persists_retry_pending_raw_event(tmp_path: Path) -> None:
    sender = RetryFailureSender()
    routing = make_test_routing_service(tmp_path)
    service = TelegramGatewayService(routing=routing, sender=sender)
    inbound = InboundMessage(
        channel="telegram",
        external_user_id="777",
        external_chat_id="12345",
        text="проверка",
        external_message_id="11",
        external_event_type="message",
        external_event_id="telegram:message:12345:11",
        raw_event={"message": {"message_id": 11, "text": "проверка", "chat": {"id": 12345}, "from": {"id": 777}}},
    )
    service._mark_retry_pending(inbound, "temporary_failure")
    with routing.session_factory() as session:
        event = session.query(TransportEvent).filter(TransportEvent.dedupe_key == "telegram:message:12345:11").one()
        assert event.status == "retry_pending"
        assert event.retry_attempts >= 1
        assert event.available_at is not None
        assert event.payload_json["message"]["text"] == "проверка"
    assert sender.calls == []


def test_telegram_gateway_retry_pending_uses_future_backoff_and_exhausts_to_terminal_status(tmp_path: Path) -> None:
    sender = RetryFailureSender()
    routing = make_test_routing_service(tmp_path)
    service = TelegramGatewayService(routing=routing, sender=sender)
    inbound = InboundMessage(
        channel="telegram",
        external_user_id="777",
        external_chat_id="12345",
        text="проверка",
        external_message_id="12",
        external_event_type="message",
        external_event_id="telegram:message:12345:12",
        received_at=datetime.now(UTC),
        raw_event={"message": {"message_id": 12, "text": "проверка", "chat": {"id": 12345}, "from": {"id": 777}}},
    )
    service._mark_retry_pending(inbound, "temporary_failure")

    with routing.session_factory() as session:
        event = session.query(TransportEvent).filter(TransportEvent.dedupe_key == "telegram:message:12345:12").one()
        first_available = event.available_at
        first_attempts = event.retry_attempts

    assert service._normalized_dt(first_available) > service._normalized_dt(inbound.received_at)
    assert first_attempts == 1

    service.process_due_retries(now=first_available - timedelta(milliseconds=1))
    with routing.session_factory() as session:
        event = session.query(TransportEvent).filter(TransportEvent.dedupe_key == "telegram:message:12345:12").one()
        assert event.status == "retry_pending"
        assert event.retry_attempts == 1


def test_telegram_gateway_restart_retries_due_transport_event_without_queue_state(tmp_path: Path) -> None:
    sender = RecordingSender()
    routing = make_test_routing_service(tmp_path)
    service = TelegramGatewayService(routing=routing, sender=sender)
    inbound = InboundMessage(
        channel="telegram",
        external_user_id="777",
        external_chat_id="12345",
        text="проверка",
        external_message_id="13",
        external_event_type="message",
        external_event_id="telegram:message:12345:13",
        received_at=datetime.now(UTC),
        raw_event={"message": {"message_id": 13, "text": "проверка", "chat": {"id": 12345}, "from": {"id": 777}}},
    )
    service._mark_retry_pending(inbound, "temporary_failure")

    processed = service.process_due_retries(now=datetime.now(UTC) + timedelta(days=1))

    assert processed == 1
    assert [call for call in sender.calls if call[0] == "message"]


def test_telegram_gateway_suppresses_stale_retry_when_newer_inbound_exists(tmp_path: Path) -> None:
    sender = RetryFailureSender()
    routing = make_test_routing_service(tmp_path)
    service = TelegramGatewayService(routing=routing, sender=sender)
    first = InboundMessage(
        channel="telegram",
        external_user_id="777",
        external_chat_id="12345",
        text="первый",
        external_message_id="14",
        external_event_type="message",
        external_event_id="telegram:message:12345:14",
        received_at=datetime.now(UTC),
        raw_event={"message": {"message_id": 14, "text": "первый", "chat": {"id": 12345}, "from": {"id": 777}}},
    )
    service._mark_retry_pending(first, "temporary_failure")
    service.handle_update({"update_id": 15, "message": {"message_id": 15, "text": "второй", "chat": {"id": 12345}, "from": {"id": 777}}})

    processed = service.process_due_retries(now=datetime.now(UTC) + timedelta(days=1))

    assert processed == 1
    assert sender.calls == []


def test_telegram_gateway_suppresses_stale_retry_when_human_override_active(tmp_path: Path) -> None:
    sender = RetryFailureSender()
    routing = make_test_routing_service(tmp_path)
    service = TelegramGatewayService(routing=routing, sender=sender)
    inbound = InboundMessage(
        channel="telegram",
        external_user_id="777",
        external_chat_id="12345",
        text="проверка",
        external_message_id="16",
        external_event_type="message",
        external_event_id="telegram:message:12345:16",
        received_at=datetime.now(UTC),
        raw_event={"message": {"message_id": 16, "text": "проверка", "chat": {"id": 12345}, "from": {"id": 777}}},
    )
    service._mark_retry_pending(inbound, "temporary_failure")
    with routing.session_factory() as session:
        from app.services.case_resolution import ensure_conversation
        from app.services.persistence import (
            activate_human_override,
            get_or_create_conversation_transport_state,
        )

        conversation = ensure_conversation(session, channel="telegram", external_chat_id="12345")
        state = get_or_create_conversation_transport_state(session, conversation_id=conversation.id, platform="telegram")
        activate_human_override(session, state, silence_seconds=3600)
        session.commit()

    assert service.process_due_retries(now=datetime.now(UTC) + timedelta(minutes=1)) == 1
    assert sender.calls == []
