from datetime import UTC, datetime, timedelta
import sys
import types

from fastapi.testclient import TestClient

from app.api.deps import get_viewer_push_service
from app.core.config import settings
from app.core.db import Base, make_session_factory as make_db_session_factory
from app.main import app
from app.models.case import SupportCase
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.viewer_notification_outbox import ViewerNotificationOutbox
from app.models.viewer_push_subscription import ViewerPushSubscription
from app.schemas.message import InboundMessage
from app.services.persistence import persist_inbound_message
from app.services.viewer_push_service import ViewerPushService


client = TestClient(app)


def make_session_factory(tmp_path):
    session_factory = make_db_session_factory(f"sqlite+pysqlite:///{tmp_path / 'viewer_push.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    return session_factory


def reset_test_state() -> None:
    app.dependency_overrides.clear()
    client.cookies.clear()


def configure_push(monkeypatch, *, enabled: bool = True) -> None:
    monkeypatch.setattr(settings, "viewer_auth_enabled", True)
    monkeypatch.setattr(settings, "viewer_auth_key", "test-viewer-key")
    monkeypatch.setattr(settings, "viewer_auth_cookie_name", "viewer_auth")
    monkeypatch.setattr(settings, "viewer_auth_session_days", 365)
    monkeypatch.setattr(settings, "viewer_push_enabled", enabled)
    monkeypatch.setattr(settings, "viewer_push_vapid_public_key", "BNz_test_public_key")


def login() -> None:
    response = client.post("/api/viewer/auth/login", json={"key": "test-viewer-key"})
    assert response.status_code == 204


def test_inbound_vk_message_creates_pending_viewer_notification_outbox_record(tmp_path, monkeypatch) -> None:
    configure_push(monkeypatch)
    session_factory = make_session_factory(tmp_path)

    with session_factory() as session:
        conversation = Conversation(external_id="vk:123")
        session.add(conversation)
        session.flush()
        support_case = SupportCase(conversation_id=conversation.id, status="open", route_mode="answer")
        session.add(support_case)
        session.flush()

        message = persist_inbound_message(
            session,
            support_case.id,
            InboundMessage(channel="vk", external_user_id="123", external_chat_id="123", text="Новая реплика"),
        )
        session.commit()

        outbox = session.query(ViewerNotificationOutbox).one()

    assert message.id == outbox.message_id
    assert outbox.conversation_id == conversation.id
    assert outbox.status == "pending"
    assert outbox.event_type == "viewer_user_message_received"


def test_telegram_inbound_message_does_not_create_vk_viewer_notification(tmp_path, monkeypatch) -> None:
    configure_push(monkeypatch)
    session_factory = make_session_factory(tmp_path)

    with session_factory() as session:
        conversation = Conversation(external_id="telegram:123")
        session.add(conversation)
        session.flush()
        support_case = SupportCase(conversation_id=conversation.id, status="open", route_mode="answer")
        session.add(support_case)
        session.flush()
        persist_inbound_message(
            session,
            support_case.id,
            InboundMessage(channel="telegram", external_user_id="123", external_chat_id="123", text="Не для VK viewer"),
        )
        session.commit()

        assert session.query(ViewerNotificationOutbox).count() == 0


def test_authenticated_viewer_can_create_and_replace_push_subscription(tmp_path, monkeypatch) -> None:
    reset_test_state()
    configure_push(monkeypatch)
    session_factory = make_session_factory(tmp_path)
    service = ViewerPushService(session_factory=session_factory)
    app.dependency_overrides[get_viewer_push_service] = lambda: service
    try:
        login()
        first = client.post(
            "/api/viewer/push/subscriptions",
            json={
                "endpoint": "https://push.example/subscription-1",
                "keys": {"p256dh": "first-key", "auth": "first-auth"},
            },
        )
        replacement = client.post(
            "/api/viewer/push/subscriptions",
            json={
                "endpoint": "https://push.example/subscription-1",
                "keys": {"p256dh": "replacement-key", "auth": "replacement-auth"},
            },
        )
    finally:
        reset_test_state()

    assert first.status_code == 201
    assert replacement.status_code == 204
    with session_factory() as session:
        subscriptions = session.query(ViewerPushSubscription).all()
    assert len(subscriptions) == 1
    assert subscriptions[0].p256dh == "replacement-key"
    assert subscriptions[0].auth == "replacement-auth"
    assert subscriptions[0].enabled is True


def test_push_config_and_subscription_require_viewer_auth(tmp_path, monkeypatch) -> None:
    reset_test_state()
    configure_push(monkeypatch)
    session_factory = make_session_factory(tmp_path)
    service = ViewerPushService(session_factory=session_factory)
    app.dependency_overrides[get_viewer_push_service] = lambda: service
    try:
        config = client.get("/api/viewer/push/config")
        subscription = client.post(
            "/api/viewer/push/subscriptions",
            json={"endpoint": "https://push.example/subscription-1", "keys": {"p256dh": "key", "auth": "auth"}},
        )
    finally:
        reset_test_state()

    assert config.status_code == 401
    assert subscription.status_code == 401


def test_pending_outbox_is_delivered_without_message_text_in_payload(tmp_path, monkeypatch) -> None:
    configure_push(monkeypatch)
    session_factory = make_session_factory(tmp_path)
    delivered = []
    service = ViewerPushService(
        session_factory=session_factory,
        send_web_push=lambda subscription, payload: delivered.append((subscription, payload)),
    )

    with session_factory() as session:
        conversation = Conversation(external_id="vk:123")
        session.add(conversation)
        session.flush()
        support_case = SupportCase(conversation_id=conversation.id, status="open", route_mode="answer")
        session.add(support_case)
        session.flush()
        inbound = Message(case_id=support_case.id, role="user", content="Секретный текст реплики", created_at=datetime.now(UTC))
        session.add(inbound)
        session.flush()
        subscription = ViewerPushSubscription(
            endpoint="https://push.example/subscription-1", p256dh="key", auth="auth", enabled=True
        )
        outbox = ViewerNotificationOutbox(
            event_type="viewer_user_message_received",
            conversation_id=conversation.id,
            message_id=inbound.id,
            status="pending",
        )
        session.add_all([subscription, outbox])
        session.commit()

    assert service.process_once() == 1
    assert len(delivered) == 1
    assert delivered[0][1] == {
        "type": "viewer_user_message",
        "conversation_id": "vk:123",
        "case_id": support_case.id,
    }
    with session_factory() as session:
        assert session.query(ViewerNotificationOutbox).one().status == "delivered"


def test_push_sender_uses_high_urgency_and_one_day_ttl(monkeypatch) -> None:
    configure_push(monkeypatch)
    monkeypatch.setattr(settings, "viewer_push_vapid_private_key", "test-private-key")
    monkeypatch.setattr(settings, "viewer_push_vapid_subject", "mailto:test@example.com")
    monkeypatch.setattr(settings, "viewer_push_ttl_seconds", 86400)
    monkeypatch.setattr(settings, "viewer_push_urgency", "high")
    captured = {}
    module = types.ModuleType("pywebpush")
    module.webpush = lambda **kwargs: captured.update(kwargs)
    monkeypatch.setitem(sys.modules, "pywebpush", module)

    ViewerPushService._send_web_push(
        ViewerPushSubscription(endpoint="https://push.example/subscription-1", p256dh="key", auth="auth", enabled=True),
        {"type": "viewer_user_message", "conversation_id": "vk:123", "case_id": 7},
    )

    assert captured["ttl"] == 86400
    assert captured["headers"] == {"Urgency": "high"}


def test_stale_processing_outbox_event_is_reclaimed_for_delivery(tmp_path, monkeypatch) -> None:
    configure_push(monkeypatch)
    session_factory = make_session_factory(tmp_path)
    delivered = []
    service = ViewerPushService(session_factory=session_factory, send_web_push=lambda subscription, payload: delivered.append(payload))

    with session_factory() as session:
        conversation = Conversation(external_id="vk:123")
        session.add(conversation)
        session.flush()
        support_case = SupportCase(conversation_id=conversation.id, status="open", route_mode="answer")
        session.add(support_case)
        session.flush()
        inbound = Message(case_id=support_case.id, role="user", content="Новая реплика", created_at=datetime.now(UTC))
        session.add(inbound)
        session.flush()
        subscription = ViewerPushSubscription(endpoint="https://push.example/subscription-1", p256dh="key", auth="auth", enabled=True)
        outbox = ViewerNotificationOutbox(
            event_type="viewer_user_message_received",
            conversation_id=conversation.id,
            message_id=inbound.id,
            status="processing",
            updated_at=datetime.now(UTC) - timedelta(minutes=3),
        )
        session.add_all([inbound, subscription, outbox])
        session.commit()

    assert service.process_once() == 1
    assert delivered
    with session_factory() as session:
        assert session.query(ViewerNotificationOutbox).one().status == "delivered"
