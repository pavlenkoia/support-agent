from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, text

from app.core.db import Base, make_session_factory
from app.models.conversation import Conversation
from app.models.conversation_transport_state import ConversationTransportState
from app.models.message import Message
from app.models.outbound_transport_send import OutboundTransportSend
from app.models.transport_event import TransportEvent
from app.models.user import User
from app.services.inbound_queue import InboundQueue
from app.services.persistence import (
    activate_human_override,
    get_or_create_conversation_transport_state,
    persist_transport_event,
)
from app.services.vk_gateway import VKGatewayService


class RecordingVKSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.next_message_id = 9001
        self.next_random_id = 700001

    def send_message(self, *, peer_id: str, text: str, random_id: str | None = None) -> dict:
        self.calls.append((peer_id, text))
        external_message_id = str(self.next_message_id)
        resolved_random_id = random_id or str(self.next_random_id)
        self.next_message_id += 1
        self.next_random_id += 1
        return {
            "ok": True,
            "sent": True,
            "peer_id": peer_id,
            "text": text,
            "random_id": resolved_random_id,
            "external_message_id": external_message_id,
            "sent_at": datetime.now(UTC),
            "vk_response": {"ok": True, "response": int(external_message_id)},
        }


class RaisingVKSender:
    def send_message(self, *, peer_id: str, text: str, random_id: str | None = None) -> dict:
        _ = (peer_id, text, random_id)
        raise RuntimeError("vk sender socket closed")


class PendingJournalAssertingVKSender:
    """Requires the gateway to journal a generated VK random_id before sending."""

    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory
        self.random_ids: list[str] = []

    def send_message(self, *, peer_id: str, text: str, random_id: str | None = None) -> dict:
        assert random_id is not None
        with self.session_factory() as session:
            pending = session.scalar(
                select(OutboundTransportSend).where(
                    OutboundTransportSend.platform == "vk",
                    OutboundTransportSend.peer_external_id == peer_id,
                    OutboundTransportSend.random_id == random_id,
                    OutboundTransportSend.send_status == "pending",
                    OutboundTransportSend.content_text == text,
                )
            )
            assert pending is not None, "outbound VK message must be journaled before messages.send"
        self.random_ids.append(random_id)
        return {
            "ok": True,
            "sent": True,
            "peer_id": peer_id,
            "text": text,
            "random_id": random_id,
            "external_message_id": "9001",
            "sent_at": datetime.now(UTC),
            "vk_response": {"ok": True, "response": 9001},
        }


class InterleavingVKSender(PendingJournalAssertingVKSender):
    """Emits VK's outbound event before messages.send returns."""

    def __init__(self, session_factory) -> None:
        super().__init__(session_factory)
        self.gateway: VKGatewayService | None = None

    def send_message(self, *, peer_id: str, text: str, random_id: str | None = None) -> dict:
        delivery = super().send_message(peer_id=peer_id, text=text, random_id=random_id)
        assert self.gateway is not None
        reply_result = self.gateway.handle_event(
            {
                "type": "message_reply",
                "group_id": 55,
                "object": {
                    "message": {
                        "id": int(delivery["external_message_id"]),
                        "peer_id": int(peer_id),
                        "from_id": -55,
                        "out": 1,
                        "text": text,
                        "date": int(datetime.now(UTC).timestamp()),
                    }
                },
            }
        )
        assert reply_result["sent_by"] == "bot"
        return delivery


class FailingVKSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def send_message(self, *, peer_id: str, text: str, random_id: str | None = None) -> dict:
        self.calls.append((peer_id, text))
        return {
            "ok": False,
            "sent": False,
            "peer_id": peer_id,
            "text": text,
            "random_id": random_id or "700999",
            "external_message_id": None,
            "sent_at": datetime.now(UTC),
            "reason": "vk_api_error",
            "vk_response": {
                "ok": False,
                "reason": "vk_api_error",
                "error": {
                    "error_code": 901,
                    "error_msg": "Can't send messages for users from blacklist",
                    "request_params": [{"key": "peer_id", "value": "2005"}],
                },
            },
        }


class RecordingVKProfileClient:
    def __init__(
        self,
        responses: dict[str, dict] | None = None,
        *,
        history: list[dict] | None = None,
        ok: bool = True,
    ) -> None:
        self.responses = responses or {}
        self.history = history or []
        self.ok = ok
        self.calls: list[list[str]] = []
        self.history_calls: list[str] = []

    def get_history(self, peer_id: str | int, *, count: int = 20) -> dict:
        _ = count
        self.history_calls.append(str(peer_id))
        return {"ok": self.ok, "response": {"items": self.history} if self.ok else {}}

    def get_users(self, user_ids: list[str | int], *, fields: list[str] | None = None) -> dict:
        normalized_ids = [str(user_id) for user_id in user_ids]
        self.calls.append(normalized_ids)
        if not self.ok:
            return {"ok": False, "reason": "vk_api_error"}
        return {
            "ok": True,
            "response": [self.responses.get(user_id, {"id": int(user_id), "first_name": "VK", "last_name": user_id}) for user_id in normalized_ids],
        }


class StubRouting:
    def __init__(self, *, response_text: str = "Готовый ответ") -> None:
        self.response_text = response_text
        self.handled_payloads = []
        self.recorded_outbound = []

    def handle_inbound(self, payload, *, persist_inbound: bool = True) -> dict:
        self.handled_payloads.append(payload)
        return {
            "case": {"conversation_id": 1, "case_id": 1, "case_status": "open"},
            "outcome": {"outcome_payload": {"response_text": self.response_text}},
        }

    def record_outbound_message(self, case_id: int, text: str) -> None:
        self.recorded_outbound.append((case_id, text))


class EmptyReplyRouting(StubRouting):
    def handle_inbound(self, payload, *, persist_inbound: bool = True) -> dict:
        _ = (payload, persist_inbound)
        return {
            "case": {"conversation_id": 1, "case_id": 1, "case_status": "resolved"},
            "outcome": {"outcome_type": "cannot_answer", "outcome_payload": {"response_text": ""}},
        }


class AlwaysRetryRouting(StubRouting):
    def handle_inbound(self, payload, *, persist_inbound: bool = True) -> dict:
        _ = (payload, persist_inbound)
        return {
            "case": {"conversation_id": 1, "case_id": 1, "case_status": "retry_pending"},
            "outcome": {"outcome_type": "retry_pending", "outcome_payload": {"response_text": ""}},
        }


class DeferredRetryRouting(StubRouting):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0
        self.persist_flags: list[bool] = []

    def handle_inbound(self, payload, *, persist_inbound: bool = True) -> dict:
        self.calls += 1
        self.persist_flags.append(persist_inbound)
        if self.calls == 1:
            return {
                "case": {"conversation_id": 1, "case_id": 1, "case_status": "retry_pending"},
                "outcome": {"outcome_type": "retry_pending", "outcome_payload": {"response_text": ""}},
            }
        return {
            "case": {"conversation_id": 1, "case_id": 1, "case_status": "resolved"},
            "outcome": {"outcome_type": "answer", "outcome_payload": {"response_text": "Ответ после повтора"}},
        }


class RetryBecomesStaleDuringRouting(StubRouting):
    def __init__(self, session_factory) -> None:
        super().__init__(response_text="Старый ответ")
        self.session_factory = session_factory
        self.calls = 0

    def handle_inbound(self, payload, *, persist_inbound: bool = True) -> dict:
        self.calls += 1
        if self.calls == 1:
            return {
                "case": {"conversation_id": 1, "case_id": 1, "case_status": "retry_pending"},
                "outcome": {"outcome_type": "retry_pending", "outcome_payload": {"response_text": ""}},
            }
        with self.session_factory() as session:
            conversation = session.scalar(select(Conversation).where(Conversation.external_id == f"vk:{payload.external_chat_id}"))
            state = get_or_create_conversation_transport_state(session, conversation_id=conversation.id, platform="vk")
            state.last_inbound_external_message_id = "101"
            session.commit()
        return super().handle_inbound(payload, persist_inbound=persist_inbound)


class RetryClaimRouting(DeferredRetryRouting):
    def __init__(self, session_factory) -> None:
        super().__init__()
        self.session_factory = session_factory
        self.claimed_status: str | None = None

    def handle_inbound(self, payload, *, persist_inbound: bool = True) -> dict:
        self.calls += 1
        self.persist_flags.append(persist_inbound)
        if self.calls == 1:
            return {
                "case": {"conversation_id": 1, "case_id": 1, "case_status": "retry_pending"},
                "outcome": {"outcome_type": "retry_pending", "outcome_payload": {"response_text": ""}},
            }
        with self.session_factory() as session:
            stored = session.scalar(select(TransportEvent).where(TransportEvent.dedupe_key == "vk:message_new:2000:100"))
            self.claimed_status = stored.status if stored is not None else None
        return {
            "case": {"conversation_id": 1, "case_id": 1, "case_status": "resolved"},
            "outcome": {"outcome_type": "answer", "outcome_payload": {"response_text": "Ответ после claim"}},
        }


class RaceRouting(StubRouting):
    def __init__(self, session_factory, *, response_text: str = "Готовый ответ") -> None:
        super().__init__(response_text=response_text)
        self.session_factory = session_factory

    def handle_inbound(self, payload, *, persist_inbound: bool = True) -> dict:
        result = super().handle_inbound(payload, persist_inbound=persist_inbound)
        with self.session_factory() as session:
            conversation = session.scalar(select(Conversation).where(Conversation.external_id == f"vk:{payload.external_chat_id}"))
            state = get_or_create_conversation_transport_state(session, conversation_id=conversation.id, platform="vk")
            activate_human_override(session, state, admin_replied_at=datetime.now(UTC), silence_seconds=3600)
            session.commit()
        return result


class ImmediateQueue:
    """Legacy gateway tests exercise delivery after an explicitly due batch."""

    def __init__(self, processor) -> None:
        self.queue = InboundQueue(processor, quiet_seconds=0, max_wait_seconds=0)

    def submit(self, inbound):
        batch = self.queue.submit(inbound)
        self.queue.flush_due(now=datetime.now(UTC), background=False)
        return batch

    def cancel(self, channel: str, external_chat_id: str) -> None:
        self.queue.cancel(channel, external_chat_id)


def make_service(tmp_path: Path, *, routing=None, sender=None, client=None) -> tuple[VKGatewayService, Any]:
    db_path = tmp_path / "vk.db"
    session_factory = make_session_factory(f"sqlite+pysqlite:///{db_path}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    service = VKGatewayService(
        routing=routing or StubRouting(),
        sender=sender or RecordingVKSender(),
        client=client,
        session_factory=session_factory,
        override_silence_seconds=3600,
    )
    service.queue = ImmediateQueue(service._process_generation)
    return service, session_factory


def test_vk_gateway_coalesces_two_messages_into_one_runtime_turn(tmp_path: Path) -> None:
    sender = RecordingVKSender()
    routing = StubRouting(response_text="Ответ на полный запрос")
    service, session_factory = make_service(tmp_path, routing=routing, sender=sender)
    service.queue = InboundQueue(service._process_generation, quiet_seconds=5, max_wait_seconds=15)
    now = datetime.now(UTC)

    for message_id, text_value in [(201, "Муж хочет прыгнуть с парашютом😁"), (202, "Хочу ему сделать подарок")]:
        result = service.handle_event({
            "type": "message_new",
            "group_id": 55,
            "object": {"message": {"id": message_id, "peer_id": 2201, "from_id": 3201, "text": text_value, "date": int(now.timestamp())}},
        })
        assert result["queued"] is True

    assert service.queue.flush_due(now=now + timedelta(seconds=6), background=False) == 1
    assert [payload.text for payload in routing.handled_payloads] == [
        "Муж хочет прыгнуть с парашютом😁\nХочу ему сделать подарок"
    ]
    assert sender.calls == [("2201", "Ответ на полный запрос")]
    with session_factory() as session:
        messages = session.scalars(select(Message).order_by(Message.id)).all()
        assert [(message.role, message.content) for message in messages] == [
            ("user", "Муж хочет прыгнуть с парашютом😁\nХочу ему сделать подарок"),
        ]
        assert routing.recorded_outbound == [(1, "Ответ на полный запрос")]
        events = session.scalars(select(TransportEvent).order_by(TransportEvent.id)).all()
        assert [(event.external_message_id, event.status) for event in events] == [("201", "processed"), ("202", "processed")]


def test_vk_gateway_retries_pending_kb_transport_failure_without_intermediate_customer_reply(tmp_path: Path) -> None:
    sender = RecordingVKSender()
    routing = DeferredRetryRouting()
    service, session_factory = make_service(tmp_path, routing=routing, sender=sender)
    event = {
        "type": "message_new",
        "group_id": 55,
        "object": {"message": {"id": 100, "peer_id": 2000, "from_id": 3000, "text": "Есть ли ограничения по весу?", "date": 1780000000}},
    }

    first = service.handle_event(event)

    assert first["queued"] is True
    assert sender.calls == []
    with session_factory() as session:
        stored = session.scalar(select(TransportEvent).where(TransportEvent.dedupe_key == "vk:message_new:2000:100"))
        assert stored is not None
        assert stored.status == "retry_pending"
        assert stored.retry_attempts == 1
        due_at = stored.available_at

    retried = service.process_due_retries(now=due_at + timedelta(seconds=1))

    assert retried["processed"] == 1
    assert sender.calls == [("2000", "Ответ после повтора")]
    assert routing.persist_flags == [False, False]
    with session_factory() as session:
        stored = session.scalar(select(TransportEvent).where(TransportEvent.dedupe_key == "vk:message_new:2000:100"))
        assert stored is not None
        assert stored.status == "processed"


def test_vk_gateway_suppresses_stale_retry_when_newer_inbound_exists(tmp_path: Path) -> None:
    sender = RecordingVKSender()
    routing = AlwaysRetryRouting()
    service, session_factory = make_service(tmp_path, routing=routing, sender=sender)
    now = datetime.now(UTC)
    first_event = {
        "type": "message_new",
        "group_id": 55,
        "object": {"message": {"id": 100, "peer_id": 2000, "from_id": 3000, "text": "Первый вопрос", "date": int(now.timestamp())}},
    }
    newer_event = {
        "type": "message_new",
        "group_id": 55,
        "object": {"message": {"id": 101, "peer_id": 2000, "from_id": 3000, "text": "Уточняю второй вопрос", "date": int((now + timedelta(seconds=1)).timestamp())}},
    }

    service.handle_event(first_event)
    with session_factory() as session:
        first = session.scalar(select(TransportEvent).where(TransportEvent.dedupe_key == "vk:message_new:2000:100"))
        assert first is not None
        due_at = first.available_at

    service.handle_event(newer_event)
    retried = service.process_due_retries(now=due_at + timedelta(seconds=1))

    assert retried["processed"] == 2
    assert sender.calls == []
    with session_factory() as session:
        first = session.scalar(select(TransportEvent).where(TransportEvent.dedupe_key == "vk:message_new:2000:100"))
        assert first is not None
        assert first.status == "suppressed"
        assert first.error_text == "vk_retry_suppressed"


def test_vk_gateway_suppresses_retry_that_becomes_stale_during_routing(tmp_path: Path) -> None:
    sender = RecordingVKSender()
    db_path = tmp_path / "vk.db"
    session_factory = make_session_factory(f"sqlite+pysqlite:///{db_path}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    routing = RetryBecomesStaleDuringRouting(session_factory)
    service = VKGatewayService(routing=routing, sender=sender, session_factory=session_factory)
    service.queue = ImmediateQueue(service._process_generation)
    event = {
        "type": "message_new",
        "group_id": 55,
        "object": {"message": {"id": 100, "peer_id": 2000, "from_id": 3000, "text": "Первый вопрос", "date": 1780000000}},
    }

    service.handle_event(event)
    with session_factory() as session:
        stored = session.scalar(select(TransportEvent).where(TransportEvent.dedupe_key == "vk:message_new:2000:100"))
        assert stored is not None
        due_at = stored.available_at

    service.process_due_retries(now=due_at + timedelta(seconds=1))

    assert sender.calls == []
    with session_factory() as session:
        stored = session.scalar(select(TransportEvent).where(TransportEvent.dedupe_key == "vk:message_new:2000:100"))
        assert stored is not None
        assert stored.status == "suppressed"


def test_vk_gateway_claims_retry_before_running_routing(tmp_path: Path) -> None:
    db_path = tmp_path / "vk.db"
    session_factory = make_session_factory(f"sqlite+pysqlite:///{db_path}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    routing = RetryClaimRouting(session_factory)
    sender = RecordingVKSender()
    service = VKGatewayService(routing=routing, sender=sender, session_factory=session_factory)
    service.queue = ImmediateQueue(service._process_generation)
    event = {
        "type": "message_new",
        "group_id": 55,
        "object": {"message": {"id": 100, "peer_id": 2000, "from_id": 3000, "text": "Первый вопрос", "date": 1780000000}},
    }

    service.handle_event(event)
    with session_factory() as session:
        stored = session.scalar(select(TransportEvent).where(TransportEvent.dedupe_key == "vk:message_new:2000:100"))
        assert stored is not None
        due_at = stored.available_at

    service.process_due_retries(now=due_at + timedelta(seconds=1))

    assert routing.claimed_status == "processing"
    assert sender.calls == [("2000", "Ответ после claim")]


def test_vk_gateway_retry_reuses_stable_vk_random_id_after_recovery(tmp_path: Path) -> None:
    service, session_factory = make_service(tmp_path, routing=AlwaysRetryRouting())
    event = {
        "type": "message_new",
        "group_id": 55,
        "object": {"message": {"id": 100, "peer_id": 2000, "from_id": 3000, "text": "Первый вопрос", "date": 1780000000}},
    }
    service.handle_event(event)
    with session_factory() as session:
        stored = session.scalar(select(TransportEvent).where(TransportEvent.dedupe_key == "vk:message_new:2000:100"))
        assert stored is not None
        event_id = stored.id

    assert service._retry_random_id(event_id) == service._retry_random_id(event_id)
    assert 1 <= service._retry_random_id(event_id) <= 2_147_483_647


def test_vk_gateway_marks_exhausted_kb_retry_for_human_handling(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("app.services.vk_gateway.settings.kb_agent_deferred_retry_max_attempts", 0)
    service, session_factory = make_service(tmp_path, routing=AlwaysRetryRouting())
    event = {
        "type": "message_new",
        "group_id": 55,
        "object": {"message": {"id": 99, "peer_id": 1999, "from_id": 2999, "text": "Есть ли ограничения по весу?", "date": 1780000000}},
    }

    service.handle_event(event)
    with session_factory() as session:
        stored = session.scalar(select(TransportEvent).where(TransportEvent.dedupe_key == "vk:message_new:1999:99"))
        assert stored is not None
        due_at = stored.available_at

    service.process_due_retries(now=due_at + timedelta(seconds=1))

    with session_factory() as session:
        stored = session.scalar(select(TransportEvent).where(TransportEvent.dedupe_key == "vk:message_new:1999:99"))
        assert stored is not None
        assert stored.status == "waiting_human"
        assert stored.error_text == "kb_agent_retry_exhausted"


def test_vk_gateway_populates_missing_user_display_name_from_vk_profile(tmp_path: Path) -> None:
    client = RecordingVKProfileClient(
        responses={"3001": {"id": 3001, "first_name": "Иван", "last_name": "Петров"}}
    )
    service, session_factory = make_service(tmp_path, client=client)

    result = service.handle_event(
        {
            "type": "message_new",
            "group_id": 55,
            "object": {"message": {"id": 101, "peer_id": 2001, "from_id": 3001, "text": "Здравствуйте", "date": 1780000000}},
        }
    )

    assert result["queued"] is True
    assert client.calls == [["3001"]]
    with session_factory() as session:
        user = session.scalar(select(User).where(User.external_id == "vk:3001"))
        assert user is not None
        assert user.display_name == "Иван Петров"


def test_vk_gateway_skips_profile_lookup_when_display_name_already_exists(tmp_path: Path) -> None:
    client = RecordingVKProfileClient(
        responses={"3006": {"id": 3006, "first_name": "Новый", "last_name": "Профиль"}}
    )
    service, session_factory = make_service(tmp_path, client=client)

    with session_factory() as session:
        session.add(User(external_id="vk:3006", display_name="Старое Имя"))
        session.commit()

    result = service.handle_event(
        {
            "type": "message_new",
            "group_id": 55,
            "object": {"message": {"id": 106, "peer_id": 2006, "from_id": 3006, "text": "Здравствуйте", "date": 1780000600}},
        }
    )

    assert result["queued"] is True
    assert client.calls == []
    with session_factory() as session:
        user = session.scalar(select(User).where(User.external_id == "vk:3006"))
        assert user is not None
        assert user.display_name == "Старое Имя"


def test_vk_gateway_ignores_profile_lookup_failures_and_keeps_processing(tmp_path: Path) -> None:
    client = RecordingVKProfileClient(ok=False)
    service, session_factory = make_service(tmp_path, client=client)

    with session_factory() as session:
        session.add(User(external_id="vk:3007", display_name=None))
        session.commit()

    result = service.handle_event(
        {
            "type": "message_new",
            "group_id": 55,
            "object": {"message": {"id": 107, "peer_id": 2007, "from_id": 3007, "text": "Здравствуйте", "date": 1780000700}},
        }
    )

    assert result["queued"] is True
    assert client.calls == [["3007"]]
    with session_factory() as session:
        user = session.scalar(select(User).where(User.external_id == "vk:3007"))
        assert user is not None
        assert user.display_name is None


def test_vk_gateway_journals_outbound_before_sending_to_vk(tmp_path: Path) -> None:
    service, session_factory = make_service(tmp_path)
    sender = PendingJournalAssertingVKSender(session_factory)
    service.sender = sender

    inbound_result = service.handle_event(
        {
            "type": "message_new",
            "group_id": 55,
            "object": {"message": {"id": 111, "peer_id": 2011, "from_id": 3011, "text": "Здравствуйте", "date": 1780000000}},
        }
    )

    assert inbound_result["queued"] is True
    assert len(sender.random_ids) == 1
    with session_factory() as session:
        send = session.scalar(select(OutboundTransportSend).where(OutboundTransportSend.external_message_id == "9001"))
        assert send is not None
        assert send.send_status == "sent"


def test_vk_gateway_reconciles_reply_arriving_before_send_returns_without_human_copy(tmp_path: Path) -> None:
    service, session_factory = make_service(tmp_path)
    sender = InterleavingVKSender(session_factory)
    sender.gateway = service
    service.sender = sender

    inbound_result = service.handle_event(
        {
            "type": "message_new",
            "group_id": 55,
            "object": {"message": {"id": 112, "peer_id": 2012, "from_id": 3012, "text": "Здравствуйте", "date": 1780000000}},
        }
    )

    assert inbound_result["queued"] is True
    with session_factory() as session:
        messages = session.scalars(select(Message).order_by(Message.id)).all()
        assert [(message.role, message.content) for message in messages] == [("user", "Здравствуйте")]
        sends = session.scalars(select(OutboundTransportSend)).all()
        assert len(sends) == 1
        assert sends[0].send_status == "reconciled"
        assert sends[0].external_message_id == "9001"
        state = session.scalar(select(ConversationTransportState))
        assert state is not None
        assert state.human_override_until is None


def test_vk_gateway_reconciles_bot_message_reply_without_override(tmp_path: Path) -> None:
    sender = RecordingVKSender()
    service, session_factory = make_service(tmp_path, sender=sender)

    inbound_result = service.handle_event(
        {
            "type": "message_new",
            "group_id": 55,
            "object": {"message": {"id": 101, "peer_id": 2001, "from_id": 3001, "text": "Здравствуйте", "date": 1780000000}},
        }
    )
    assert inbound_result["queued"] is True
    assert sender.calls == [("2001", "Готовый ответ")]

    reply_result = service.handle_event(
        {
            "type": "message_reply",
            "group_id": 55,
            "object": {"message": {"id": 9001, "peer_id": 2001, "text": "Готовый ответ", "date": 1780000001}},
        }
    )
    assert reply_result["sent_by"] == "bot"

    with session_factory() as session:
        send = session.scalar(select(OutboundTransportSend).where(OutboundTransportSend.external_message_id == "9001"))
        assert send is not None
        assert send.send_status == "reconciled"
        state = session.scalar(select(ConversationTransportState))
        assert state is not None
        assert state.human_override_until is None


def test_vk_gateway_recovers_missing_inbound_context_before_persisting_admin_reply(tmp_path: Path) -> None:
    event_timestamp = 1780000100
    client = RecordingVKProfileClient(
        responses={"3002": {"id": 3002, "first_name": "Иван", "last_name": "Петров"}},
        history=[
            {
                "id": 7001,
                "peer_id": 2002,
                "from_id": -55,
                "out": 1,
                "text": "Отвечу сам",
                "date": event_timestamp,
            },
            {
                "id": 7000,
                "peer_id": 2002,
                "from_id": 3002,
                "out": 0,
                "text": "Здравствуйте, хочу прыгнуть в тандеме",
                "date": event_timestamp - 60,
            },
        ],
    )
    service, session_factory = make_service(tmp_path, client=client)

    result = service.handle_event(
        {
            "type": "message_reply",
            "group_id": 55,
            "object": {
                "id": 7001,
                "peer_id": 2002,
                "from_id": 9002,
                "text": "Отвечу сам",
                "date": event_timestamp,
            },
        }
    )

    assert result["sent_by"] == "admin"
    assert client.history_calls == ["2002"]
    assert client.calls == [["3002"]]
    with session_factory() as session:
        messages = session.scalars(select(Message).order_by(Message.id)).all()
        assert [(message.role, message.content) for message in messages] == [
            ("user", "Здравствуйте, хочу прыгнуть в тандеме"),
            ("human", "Отвечу сам"),
        ]
        assert messages[0].created_at.replace(tzinfo=UTC) == datetime.fromtimestamp(event_timestamp - 60, tz=UTC)
        assert messages[1].created_at.replace(tzinfo=UTC) == datetime.fromtimestamp(event_timestamp, tz=UTC)
        assert messages[0].support_case.conversation.external_id == "vk:2002"
        user = session.scalar(select(User).where(User.external_id == "vk:3002"))
        assert user is not None
        assert user.display_name == "Иван Петров"


def test_vk_gateway_unmatched_admin_reply_activates_override_and_suppresses_inbound(tmp_path: Path) -> None:
    sender = RecordingVKSender()
    routing = StubRouting()
    service, session_factory = make_service(tmp_path, sender=sender, routing=routing)

    admin_reply = service.handle_event(
        {
            "type": "message_reply",
            "group_id": 55,
            "object": {"message": {"id": 7001, "peer_id": 2002, "text": "Отвечу сам", "date": 1780000100}},
        }
    )
    assert admin_reply["sent_by"] == "admin"

    suppressed = service.handle_event(
        {
            "type": "message_new",
            "group_id": 55,
            "object": {"message": {"id": 102, "peer_id": 2002, "from_id": 3002, "text": "Тогда уточню", "date": 1780000200}},
        }
    )
    assert suppressed["suppressed"] is True
    assert sender.calls == []
    assert len(routing.handled_payloads) == 0

    with session_factory() as session:
        messages = session.scalars(select(Message).order_by(Message.id)).all()
        assert [(message.role, message.content) for message in messages] == [
            ("human", "Отвечу сам"),
            ("user", "Тогда уточню"),
        ]
        state = session.scalar(select(ConversationTransportState))
        assert state is not None
        assert state.human_override_until is not None
        assert state.human_override_until > state.last_admin_reply_at


def test_vk_gateway_second_admin_reply_extends_override(tmp_path: Path) -> None:
    service, session_factory = make_service(tmp_path)

    service.handle_event(
        {
            "type": "message_reply",
            "group_id": 55,
            "object": {"message": {"id": 7101, "peer_id": 2003, "text": "Первый ответ", "date": 1780000300}},
        }
    )
    service.handle_event(
        {
            "type": "message_reply",
            "group_id": 55,
            "object": {"message": {"id": 7102, "peer_id": 2003, "text": "Второй ответ", "date": 1780001500}},
        }
    )

    with session_factory() as session:
        state = session.scalar(select(ConversationTransportState))
        assert state is not None
        expected_last_admin = datetime.fromtimestamp(1780001500, tz=UTC)
        expected_override_until = expected_last_admin + timedelta(hours=1)
        assert state.last_admin_reply_at.replace(tzinfo=UTC) == expected_last_admin
        assert state.human_override_until.replace(tzinfo=UTC) == expected_override_until


def test_vk_gateway_rechecks_override_before_send_and_drops_stale_reply(tmp_path: Path) -> None:
    sender = RecordingVKSender()
    service, session_factory = make_service(tmp_path)
    service.routing = RaceRouting(session_factory)
    service.sender = sender

    result = service.handle_event(
        {
            "type": "message_new",
            "group_id": 55,
            "object": {"message": {"id": 103, "peer_id": 2004, "from_id": 3004, "text": "Есть вопрос", "date": 1780000400}},
        }
    )

    assert result["queued"] is True
    assert sender.calls == []


def test_vk_gateway_persists_structured_vk_send_error_details(tmp_path: Path) -> None:
    sender = FailingVKSender()
    service, session_factory = make_service(tmp_path, sender=sender)

    result = service.handle_event(
        {
            "type": "message_new",
            "group_id": 55,
            "object": {"message": {"id": 104, "peer_id": 2005, "from_id": 3005, "text": "Можно купить?", "date": 1780000500}},
        }
    )

    assert result["queued"] is True
    assert sender.calls == [("2005", "Готовый ответ")]

    with session_factory() as session:
        row = session.execute(
            text("select status, error_text from transport_events where dedupe_key = 'vk:message_new:2005:104'")
        ).one()
        assert row[0] == "failed"



def test_vk_gateway_marks_empty_completed_reply_for_human_handling(tmp_path: Path) -> None:
    service, session_factory = make_service(tmp_path, routing=EmptyReplyRouting())

    result = service.handle_event(
        {
            "type": "message_new",
            "group_id": 55,
            "object": {"message": {"id": 106, "peer_id": 2007, "from_id": 3007, "text": "Можно купить?", "date": 1780000700}},
        }
    )

    assert result["queued"] is True
    with session_factory() as session:
        event = session.scalar(select(TransportEvent).where(TransportEvent.dedupe_key == "vk:message_new:2007:106"))
        assert event is not None
        assert event.status == "waiting_human"
        assert event.error_text == "completed_without_customer_reply"



def test_vk_gateway_recovers_expired_received_event_by_retrying_automatically(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("app.services.vk_gateway.settings.vk_received_event_timeout_seconds", 1)
    monkeypatch.setattr("app.services.vk_gateway.settings.kb_agent_deferred_retry_delay_seconds", 0)
    sender = RecordingVKSender()
    service, session_factory = make_service(tmp_path, sender=sender)
    service.queue = InboundQueue(service._process_generation, quiet_seconds=60, max_wait_seconds=60)
    service.handle_event(
        {
            "type": "message_new",
            "group_id": 55,
            "object": {"message": {"id": 107, "peer_id": 2008, "from_id": 3008, "text": "Можно купить?", "date": 1780000800}},
        }
    )

    result = service.recover_expired_received_events(now=datetime.now(UTC) + timedelta(seconds=2))

    assert result == {"recovered": 1}
    with session_factory() as session:
        event = session.scalar(select(TransportEvent).where(TransportEvent.dedupe_key == "vk:message_new:2008:107"))
        assert event is not None
        assert event.status == "retry_pending"
        assert event.retry_attempts == 1
        assert event.error_text == "received_timeout_recovery"
        due_at = event.available_at

    assert service.process_due_retries(now=due_at + timedelta(seconds=1)) == {"processed": 1}
    assert sender.calls == [("2008", "Готовый ответ")]
    with session_factory() as session:
        event = session.scalar(select(TransportEvent).where(TransportEvent.dedupe_key == "vk:message_new:2008:107"))
        assert event is not None
        assert event.status == "processed"



def test_vk_gateway_recovers_only_legacy_timeout_waiting_human_events(tmp_path: Path) -> None:
    service, session_factory = make_service(tmp_path)
    with session_factory() as session:
        timeout_event, _ = persist_transport_event(
            session,
            platform="vk",
            event_type="message_new",
            dedupe_key="vk:message_new:2009:108",
            payload_json={"type": "message_new", "object": {"message": {"id": 108, "peer_id": 2009, "from_id": 3009, "text": "Вопрос"}}},
            external_event_id="vk:message_new:2009:108",
            external_message_id="108",
            conversation_external_id="vk:2009",
        )
        timeout_event.status = "waiting_human"
        timeout_event.error_text = "received_timeout_without_finalization"
        human_event, _ = persist_transport_event(
            session,
            platform="vk",
            event_type="message_new",
            dedupe_key="vk:message_new:2010:109",
            payload_json={"type": "message_new", "object": {"message": {"id": 109, "peer_id": 2010, "from_id": 3010, "text": "Вопрос"}}},
            external_event_id="vk:message_new:2010:109",
            external_message_id="109",
            conversation_external_id="vk:2010",
        )
        human_event.status = "waiting_human"
        human_event.error_text = "kb_agent_retry_exhausted"
        session.commit()

    assert service.recover_legacy_timeout_events(now=datetime.now(UTC)) == {"recovered": 1}
    with session_factory() as session:
        timeout_event = session.scalar(select(TransportEvent).where(TransportEvent.dedupe_key == "vk:message_new:2009:108"))
        human_event = session.scalar(select(TransportEvent).where(TransportEvent.dedupe_key == "vk:message_new:2010:109"))
        assert timeout_event.status == "retry_pending"
        assert timeout_event.error_text == "received_timeout_recovery"
        assert human_event.status == "waiting_human"
        assert human_event.error_text == "kb_agent_retry_exhausted"


def test_vk_gateway_marks_event_failed_when_sender_raises(tmp_path: Path) -> None:
    service, session_factory = make_service(tmp_path, sender=RaisingVKSender())

    result = service.handle_event(
        {
            "type": "message_new",
            "group_id": 55,
            "object": {"message": {"id": 105, "peer_id": 2006, "from_id": 3006, "text": "Можно купить?", "date": 1780000600}},
        }
    )

    assert result["queued"] is True
    with session_factory() as session:
        row = session.execute(
            text("select status, error_text from transport_events where dedupe_key = 'vk:message_new:2006:105'")
        ).one()
        assert row[0] == "failed"
        assert row[1] == "delivery_exception:RuntimeError: vk sender socket closed"
