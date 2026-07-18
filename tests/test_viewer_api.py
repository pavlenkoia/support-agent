from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from app.api.deps import get_viewer_service
from app.core.config import settings
from app.core.db import Base, make_session_factory
from app.main import app
from app.models.case import SupportCase
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.services.viewer_service import ViewerService


client = TestClient(app)


def reset_test_state() -> None:
    app.dependency_overrides.clear()
    client.cookies.clear()


def make_viewer_service(tmp_path, *, viewer_timezone: str = "Asia/Yekaterinburg") -> ViewerService:
    db_path = tmp_path / "viewer.db"
    session_factory = make_session_factory(f"sqlite+pysqlite:///{db_path}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])

    with session_factory() as session:
        vk_user = User(external_id="vk:123", display_name="Иван Петров")
        vk_conversation = Conversation(external_id="vk:123")
        telegram_conversation = Conversation(external_id="telegram:999")
        session.add_all([vk_user, vk_conversation, telegram_conversation])
        session.flush()

        vk_case = SupportCase(conversation_id=vk_conversation.id, status="open", route_mode="answer")
        tg_case = SupportCase(conversation_id=telegram_conversation.id, status="open", route_mode="answer")
        session.add_all([vk_case, tg_case])
        session.flush()

        session.add_all(
            [
                Message(
                    case_id=vk_case.id,
                    role="user",
                    content="Здравствуйте",
                    created_at=datetime(2026, 6, 30, 9, 15, tzinfo=UTC),
                ),
                Message(
                    case_id=vk_case.id,
                    role="assistant",
                    content="Добрый день",
                    created_at=datetime(2026, 6, 30, 9, 16, tzinfo=UTC),
                ),
                Message(
                    case_id=vk_case.id,
                    role="user",
                    content="Вчерашнее сообщение",
                    created_at=datetime(2026, 6, 29, 18, 0, tzinfo=UTC),
                ),
                Message(
                    case_id=tg_case.id,
                    role="user",
                    content="Не должен попасть во viewer",
                    created_at=datetime(2026, 6, 30, 11, 0, tzinfo=UTC),
                ),
            ]
        )
        session.commit()

    return ViewerService(session_factory=session_factory, viewer_timezone=viewer_timezone)


def configure_viewer_auth(monkeypatch, *, enabled: bool) -> None:
    monkeypatch.setattr(settings, "viewer_auth_enabled", enabled)
    monkeypatch.setattr(settings, "viewer_auth_key", "test-viewer-key")
    monkeypatch.setattr(settings, "viewer_auth_cookie_name", "viewer_auth")
    monkeypatch.setattr(settings, "viewer_auth_session_days", 365)


def test_viewer_dialogs_lists_only_vk_dialogs_for_selected_day(tmp_path, monkeypatch) -> None:
    reset_test_state()
    configure_viewer_auth(monkeypatch, enabled=False)
    service = make_viewer_service(tmp_path)
    app.dependency_overrides[get_viewer_service] = lambda: service
    try:
        response = client.get("/api/viewer/dialogs", params={"day": "2026-06-30"})
    finally:
        reset_test_state()

    assert response.status_code == 200
    payload = response.json()
    assert payload == [
        {
            "conversation_id": "vk:123",
            "case_id": 1,
            "display_name": "Иван Петров",
            "external_chat_id": "123",
            "last_message_at": "2026-06-30T09:16:00Z",
            "message_count": 2,
        }
    ]


def test_viewer_messages_returns_only_selected_day_messages(tmp_path, monkeypatch) -> None:
    reset_test_state()
    configure_viewer_auth(monkeypatch, enabled=False)
    service = make_viewer_service(tmp_path)
    app.dependency_overrides[get_viewer_service] = lambda: service
    try:
        response = client.get("/api/viewer/dialogs/vk:123/messages", params={"day": "2026-06-30"})
    finally:
        reset_test_state()

    assert response.status_code == 200
    payload = response.json()
    assert payload["conversation_id"] == "vk:123"
    assert payload["display_name"] == "Иван Петров"
    assert payload["day"] == str(date(2026, 6, 30))
    assert payload["messages"] == [
        {
            "id": "1",
            "sent_at": "2026-06-30T09:15:00Z",
            "direction": "inbound",
            "author_name": "Иван Петров",
            "text": "Здравствуйте",
        },
        {
            "id": "2",
            "sent_at": "2026-06-30T09:16:00Z",
            "direction": "outbound",
            "author_name": "Support Agent",
            "text": "Добрый день",
        },
    ]


def test_viewer_messages_labels_human_vk_reply_as_operator(tmp_path, monkeypatch) -> None:
    reset_test_state()
    configure_viewer_auth(monkeypatch, enabled=False)
    service = make_viewer_service(tmp_path)
    with service.session_factory() as session:
        session.add(
            Message(
                case_id=1,
                role="human",
                content="Отвечу сам",
                created_at=datetime(2026, 6, 30, 9, 17, tzinfo=UTC),
            )
        )
        session.commit()
    app.dependency_overrides[get_viewer_service] = lambda: service
    try:
        response = client.get("/api/viewer/dialogs/vk:123/messages", params={"day": "2026-06-30"})
    finally:
        reset_test_state()

    assert response.status_code == 200
    last_message = response.json()["messages"][-1]
    assert last_message["id"].isdigit()
    assert {key: value for key, value in last_message.items() if key != "id"} == {
        "sent_at": "2026-06-30T09:17:00Z",
        "direction": "outbound",
        "author_name": "Оператор VK",
        "text": "Отвечу сам",
    }


def test_viewer_messages_returns_empty_list_for_day_without_messages(tmp_path, monkeypatch) -> None:
    reset_test_state()
    configure_viewer_auth(monkeypatch, enabled=False)
    service = make_viewer_service(tmp_path)
    app.dependency_overrides[get_viewer_service] = lambda: service
    try:
        response = client.get("/api/viewer/dialogs/vk:123/messages", params={"day": "2026-07-01"})
    finally:
        reset_test_state()

    assert response.status_code == 200
    payload = response.json()
    assert payload["conversation_id"] == "vk:123"
    assert payload["messages"] == []


def test_viewer_dialogs_filters_by_local_day_boundary_not_utc(tmp_path, monkeypatch) -> None:
    reset_test_state()
    configure_viewer_auth(monkeypatch, enabled=False)
    db_path = tmp_path / "viewer_boundary.db"
    session_factory = make_session_factory(f"sqlite+pysqlite:///{db_path}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])

    with session_factory() as session:
        conversation = Conversation(external_id="vk:1061355692")
        session.add(conversation)
        session.flush()

        support_case = SupportCase(conversation_id=conversation.id, status="resolved", route_mode="answer")
        session.add(support_case)
        session.flush()

        session.add_all(
            [
                Message(
                    case_id=support_case.id,
                    role="user",
                    content="Здравствуйте",
                    created_at=datetime(2026, 7, 9, 19, 42, 0, tzinfo=UTC),
                ),
                Message(
                    case_id=support_case.id,
                    role="assistant",
                    content="Ответ",
                    created_at=datetime(2026, 7, 9, 19, 43, 5, tzinfo=UTC),
                ),
            ]
        )
        session.commit()

    service = ViewerService(session_factory=session_factory, viewer_timezone="Asia/Yekaterinburg")
    app.dependency_overrides[get_viewer_service] = lambda: service
    try:
        july_9 = client.get("/api/viewer/dialogs", params={"day": "2026-07-09"})
        july_10 = client.get("/api/viewer/dialogs", params={"day": "2026-07-10"})
        july_10_messages = client.get(
            "/api/viewer/dialogs/vk:1061355692/messages",
            params={"day": "2026-07-10"},
        )
    finally:
        reset_test_state()

    assert july_9.status_code == 200
    assert july_10.status_code == 200
    assert july_10_messages.status_code == 200

    assert july_9.json() == []
    assert july_10.json() == [
        {
            "conversation_id": "vk:1061355692",
            "case_id": 1,
            "display_name": None,
            "external_chat_id": "1061355692",
            "last_message_at": "2026-07-09T19:43:05Z",
            "message_count": 2,
        }
    ]
    assert [message["id"] for message in july_10_messages.json()["messages"]] == ["1", "2"]


def test_viewer_requires_login_when_auth_enabled(tmp_path, monkeypatch) -> None:
    reset_test_state()
    configure_viewer_auth(monkeypatch, enabled=True)
    service = make_viewer_service(tmp_path)
    app.dependency_overrides[get_viewer_service] = lambda: service
    try:
        response = client.get("/api/viewer/dialogs", params={"day": "2026-06-30"})
    finally:
        reset_test_state()

    assert response.status_code == 401
    assert response.json() == {"detail": "Viewer authentication required."}


def test_viewer_login_sets_cookie_and_allows_requests(tmp_path, monkeypatch) -> None:
    reset_test_state()
    configure_viewer_auth(monkeypatch, enabled=True)
    service = make_viewer_service(tmp_path)
    app.dependency_overrides[get_viewer_service] = lambda: service
    try:
        login_response = client.post("/api/viewer/auth/login", json={"key": "test-viewer-key"})
        dialogs_response = client.get("/api/viewer/dialogs", params={"day": "2026-06-30"})
        me_response = client.get("/api/viewer/auth/me")
    finally:
        reset_test_state()

    assert login_response.status_code == 204
    assert "viewer_auth=" in login_response.headers["set-cookie"]
    assert dialogs_response.status_code == 200
    assert me_response.status_code == 200
    assert me_response.json() == {"authenticated": True}


def test_viewer_login_rejects_invalid_key(monkeypatch) -> None:
    reset_test_state()
    configure_viewer_auth(monkeypatch, enabled=True)
    try:
        response = client.post("/api/viewer/auth/login", json={"key": "wrong-key"})
    finally:
        reset_test_state()

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid viewer access key."}


def test_viewer_logout_clears_cookie_and_requires_login_again(tmp_path, monkeypatch) -> None:
    reset_test_state()
    configure_viewer_auth(monkeypatch, enabled=True)
    service = make_viewer_service(tmp_path)
    app.dependency_overrides[get_viewer_service] = lambda: service
    try:
        login_response = client.post("/api/viewer/auth/login", json={"key": "test-viewer-key"})
        logout_response = client.post("/api/viewer/auth/logout")
        dialogs_response = client.get("/api/viewer/dialogs", params={"day": "2026-06-30"})
    finally:
        reset_test_state()

    assert login_response.status_code == 204
    assert logout_response.status_code == 204
    assert "viewer_auth=" in logout_response.headers["set-cookie"]
    assert dialogs_response.status_code == 401
