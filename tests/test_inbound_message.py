from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.core.db import Base, make_session_factory
from app.schemas.message import InboundMessage
from app.services.routing import RoutingService


class RecordingSimpleEngine:
    def answer(self, **kwargs: object) -> dict:
        return {
            "kind": "grounded_answer",
            "response_text": "Подтверждённый ответ.",
            "source_refs": ["test:01"],
            "telemetry": {"answer_engine": "simple_full_corpus_natural", "logical_llm_call_count": 1},
        }


def make_test_routing_service(tmp_path: Path) -> RoutingService:
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    return RoutingService(session_factory=session_factory, simple_answer_engine=RecordingSimpleEngine())


def test_routing_starts_new_case_when_local_calendar_day_changes(tmp_path: Path) -> None:
    routing = make_test_routing_service(tmp_path)
    yekaterinburg = ZoneInfo("Asia/Yekaterinburg")
    first = routing.handle_inbound(InboundMessage(channel="telegram", external_user_id="boundary-day", external_chat_id="boundary-day", text="Первый вопрос", received_at=datetime(2026, 8, 10, 23, 30, tzinfo=yekaterinburg)))
    second = routing.handle_inbound(InboundMessage(channel="telegram", external_user_id="boundary-day", external_chat_id="boundary-day", text="Второй вопрос", received_at=datetime(2026, 8, 11, 0, 15, tzinfo=yekaterinburg)))

    assert second["case"]["case_id"] != first["case"]["case_id"]
    assert second["context"]["recent_messages"] == []


def test_routing_starts_new_case_after_two_hours_of_inactivity(tmp_path: Path) -> None:
    routing = make_test_routing_service(tmp_path)
    yekaterinburg = ZoneInfo("Asia/Yekaterinburg")
    first_at = datetime(2026, 8, 10, 10, 0, tzinfo=yekaterinburg)
    first = routing.handle_inbound(InboundMessage(channel="telegram", external_user_id="boundary-gap", external_chat_id="boundary-gap", text="Первый вопрос", received_at=first_at))
    second = routing.handle_inbound(InboundMessage(channel="telegram", external_user_id="boundary-gap", external_chat_id="boundary-gap", text="Второй вопрос", received_at=first_at + timedelta(hours=2, minutes=1)))

    assert second["case"]["case_id"] != first["case"]["case_id"]
    assert second["context"]["recent_messages"] == []


def test_routing_keeps_same_case_within_two_hours_on_same_local_day(tmp_path: Path) -> None:
    routing = make_test_routing_service(tmp_path)
    yekaterinburg = ZoneInfo("Asia/Yekaterinburg")
    first_at = datetime(2026, 8, 10, 10, 0, tzinfo=yekaterinburg)
    first = routing.handle_inbound(InboundMessage(channel="telegram", external_user_id="boundary-continue", external_chat_id="boundary-continue", text="Первый вопрос", received_at=first_at))
    second = routing.handle_inbound(InboundMessage(channel="telegram", external_user_id="boundary-continue", external_chat_id="boundary-continue", text="Второй вопрос", received_at=first_at + timedelta(hours=1, minutes=59)))

    assert second["case"]["case_id"] == first["case"]["case_id"]
    assert second["context"]["recent_messages"] == [{"role": "user", "content": "Первый вопрос"}]
