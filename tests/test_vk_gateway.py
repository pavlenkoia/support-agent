from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, text

from app.core.db import Base, make_session_factory
from app.models.conversation import Conversation
from app.models.conversation_transport_state import ConversationTransportState
from app.models.outbound_transport_send import OutboundTransportSend
from app.models.user import User
from app.services.persistence import activate_human_override, get_or_create_conversation_transport_state
from app.services.vk_gateway import VKGatewayService


class RecordingVKSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.next_message_id = 9001
        self.next_random_id = 700001

    def send_message(self, *, peer_id: str, text: str) -> dict:
        self.calls.append((peer_id, text))
        external_message_id = str(self.next_message_id)
        random_id = str(self.next_random_id)
        self.next_message_id += 1
        self.next_random_id += 1
        return {
            "ok": True,
            "sent": True,
            "peer_id": peer_id,
            "text": text,
            "random_id": random_id,
            "external_message_id": external_message_id,
            "sent_at": datetime.now(UTC),
            "vk_response": {"ok": True, "response": int(external_message_id)},
        }


class FailingVKSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def send_message(self, *, peer_id: str, text: str) -> dict:
        self.calls.append((peer_id, text))
        return {
            "ok": False,
            "sent": False,
            "peer_id": peer_id,
            "text": text,
            "random_id": "700999",
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
    def __init__(self, responses: dict[str, dict] | None = None, *, ok: bool = True) -> None:
        self.responses = responses or {}
        self.ok = ok
        self.calls: list[list[str]] = []

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

    def handle_inbound(self, payload) -> dict:
        self.handled_payloads.append(payload)
        return {
            "case": {"conversation_id": 1, "case_id": None, "case_status": "open"},
            "outcome": {"outcome_payload": {"response_text": self.response_text}},
        }

    def record_outbound_message(self, case_id: int, text: str) -> None:
        self.recorded_outbound.append((case_id, text))


class RaceRouting(StubRouting):
    def __init__(self, session_factory, *, response_text: str = "Готовый ответ") -> None:
        super().__init__(response_text=response_text)
        self.session_factory = session_factory

    def handle_inbound(self, payload) -> dict:
        result = super().handle_inbound(payload)
        with self.session_factory() as session:
            conversation = session.scalar(select(Conversation).where(Conversation.external_id == f"vk:{payload.external_chat_id}"))
            state = get_or_create_conversation_transport_state(session, conversation_id=conversation.id, platform="vk")
            activate_human_override(session, state, admin_replied_at=datetime.now(UTC), silence_seconds=3600)
            session.commit()
        return result


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
    return service, session_factory


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

    assert result["suppressed"] is False
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

    assert result["suppressed"] is False
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

    assert result["suppressed"] is False
    assert client.calls == [["3007"]]
    with session_factory() as session:
        user = session.scalar(select(User).where(User.external_id == "vk:3007"))
        assert user is not None
        assert user.display_name is None


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
    assert inbound_result["suppressed"] is False
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
        messages_count = session.execute(text("select count(*) from messages")).scalar_one()
        assert messages_count == 1
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

    assert result["suppressed"] is True
    assert result["reason"] == "human_override_activated_before_send"
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

    assert result["suppressed"] is False
    assert result["delivery"]["sent"] is False

    with session_factory() as session:
        row = session.execute(
            text("select status, error_text from transport_events where dedupe_key = 'vk:message_new:2005:104'")
        ).one()
        assert row[0] == "failed"
        assert row[1] == (
            "vk_api_error; error_code=901; "
            "error_msg=Can't send messages for users from blacklist"
        )
