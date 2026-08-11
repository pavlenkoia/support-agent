from pathlib import Path

import pytest

from app.core.db import Base, make_session_factory
from app.schemas.message import InboundMessage
from app.services.routing import RoutingService


class RecordingSimpleEngine:
    def __init__(self, *, kind: str = "grounded_answer", response_text: str = "Подтверждённый ответ.") -> None:
        self.kind = kind
        self.response_text = response_text
        self.calls: list[dict] = []

    def answer(self, **kwargs: object) -> dict:
        self.calls.append(kwargs)
        return {
            "kind": self.kind,
            "response_text": self.response_text,
            "source_refs": ["compiled/concepts/pricing.md"] if self.kind == "grounded_answer" else [],
            "telemetry": {"answer_engine": "simple_full_corpus", "logical_llm_call_count": 1},
        }


class ForbiddenLegacyOwner:
    def run(self, **kwargs: object) -> dict:
        raise AssertionError("legacy orchestrator must not run in simple mode")


class ForbiddenLegacyDependency:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"legacy dependency must not be used in simple mode: {name}")


class TextOnlyPolicy:
    def finalize_customer_text(self, text: str, *, first_reply_in_dialogue: bool) -> str:
        return f"sanitized:{text}"

    def render_out_of_scope(self) -> str:
        return "safe-out-of-scope"

    def render_simple_cannot_answer(self) -> str:
        return "safe-cannot-answer"


@pytest.mark.parametrize("channel", ["telegram", "vk", "http", "internal_test"])
def test_simple_mode_routes_once_without_legacy_orchestrator(tmp_path: Path, channel: str) -> None:
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'routing.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    engine = RecordingSimpleEngine()
    routing = RoutingService(
        session_factory=session_factory,
        answer_engine_mode="simple_full_corpus",
        simple_answer_engine=engine,
        orchestrator=ForbiddenLegacyOwner(),
        kb_agent=ForbiddenLegacyDependency(),
        summary_service=ForbiddenLegacyDependency(),
        direct_llm=ForbiddenLegacyDependency(),
    )

    result = routing.handle_inbound(
        InboundMessage(channel=channel, external_user_id="user", external_chat_id="chat", text="Сколько стоит?")
    )

    assert result["outcome"]["outcome_type"] == "answer"
    assert result["route"]["answer_engine"] == "simple_full_corpus"
    assert len(engine.calls) == 1
    assert engine.calls[0]["question"] == "Сколько стоит?"


def test_simple_policy_can_sanitize_text_but_cannot_change_engine_kind(tmp_path: Path) -> None:
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'routing-policy.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    routing = RoutingService(
        session_factory=session_factory,
        answer_engine_mode="simple_full_corpus",
        simple_answer_engine=RecordingSimpleEngine(kind="out_of_scope", response_text="Не мой вопрос."),
        policy=TextOnlyPolicy(),
    )

    result = routing.handle_inbound(
        InboundMessage(channel="test", external_user_id="user", external_chat_id="chat", text="Как сварить суп?")
    )

    assert result["route"]["outcome_kind"] == "out_of_scope"
    assert result["outcome"]["outcome_type"] == "out_of_scope"
    assert result["outcome"]["outcome_payload"]["response_text"] == "safe-out-of-scope"


def test_simple_cannot_answer_never_uses_legacy_contact_fallback(tmp_path: Path) -> None:
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'routing-cannot-answer.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    routing = RoutingService(
        session_factory=session_factory,
        answer_engine_mode="simple_full_corpus",
        simple_answer_engine=RecordingSimpleEngine(kind="cannot_answer", response_text=""),
        policy=TextOnlyPolicy(),
    )

    result = routing.handle_inbound(
        InboundMessage(channel="test", external_user_id="user", external_chat_id="chat", text="Нет данных")
    )

    assert result["route"]["outcome_kind"] == "cannot_answer"
    assert result["outcome"]["outcome_payload"]["response_text"] == "safe-cannot-answer"
