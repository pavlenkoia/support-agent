from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from app.api.deps import get_viewer_service
from app.core.db import Base, make_session_factory
from app.main import app
from app.models.case import SupportCase
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.services.viewer_service import ViewerService


client = TestClient(app)


def make_viewer_service(tmp_path) -> ViewerService:
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

    return ViewerService(session_factory=session_factory)


def test_viewer_dialogs_lists_only_vk_dialogs_for_selected_day(tmp_path) -> None:
    service = make_viewer_service(tmp_path)
    app.dependency_overrides[get_viewer_service] = lambda: service
    try:
        response = client.get("/api/viewer/dialogs", params={"day": "2026-06-30"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload == [
        {
            "conversation_id": "vk:123",
            "case_id": 1,
            "display_name": "Иван Петров",
            "external_chat_id": "123",
            "last_message_time": "09:16:00",
            "message_count": 2,
        }
    ]


def test_viewer_messages_returns_only_selected_day_messages(tmp_path) -> None:
    service = make_viewer_service(tmp_path)
    app.dependency_overrides[get_viewer_service] = lambda: service
    try:
        response = client.get("/api/viewer/dialogs/vk:123/messages", params={"day": "2026-06-30"})
    finally:
        app.dependency_overrides.clear()

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


def test_viewer_messages_returns_empty_list_for_day_without_messages(tmp_path) -> None:
    service = make_viewer_service(tmp_path)
    app.dependency_overrides[get_viewer_service] = lambda: service
    try:
        response = client.get("/api/viewer/dialogs/vk:123/messages", params={"day": "2026-07-01"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["conversation_id"] == "vk:123"
    assert payload["messages"] == []
