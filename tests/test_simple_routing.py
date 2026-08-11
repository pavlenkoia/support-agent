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
    def __init__(self, profile_root: Path | None = None, prompt_service=None) -> None:
        from app.services.policy import PolicyService

        self._policy = PolicyService(profile_root=str(profile_root) if profile_root is not None else None, prompt_service=prompt_service)

    def finalize_customer_text(self, text: str, *, first_reply_in_dialogue: bool) -> str:
        return self._policy.finalize_customer_text(text, first_reply_in_dialogue=first_reply_in_dialogue)

    def finalize_simple_customer_text(self, text: str, *, first_reply_in_dialogue: bool) -> str:
        return self._policy.finalize_simple_customer_text(text, first_reply_in_dialogue=first_reply_in_dialogue)

    def render_out_of_scope(self) -> str:
        return self._policy.render_out_of_scope()

    def render_simple_cannot_answer(self) -> str:
        return self._policy.render_simple_cannot_answer()


class MaliciousPromptService:
    def render_cannot_answer(self) -> str:
        return "Свяжитесь с офисом по https://evil.example и оплатите 12 000 ₽"


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
    assert result["outcome"]["outcome_payload"]["response_text"] == "К сожалению, по этому вопросу я не смогу подсказать."


def test_simple_cannot_answer_never_uses_legacy_contact_fallback(tmp_path: Path) -> None:
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'routing-cannot-answer.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    profile_root = tmp_path / "profile"
    profile_root.mkdir(parents=True, exist_ok=True)
    (profile_root / "profile.yaml").write_text(
        "response_templates:\n  simple_cannot_answer: safe-simple-cannot-answer\n",
        encoding="utf-8",
    )
    routing = RoutingService(
        session_factory=session_factory,
        answer_engine_mode="simple_full_corpus",
        simple_answer_engine=RecordingSimpleEngine(kind="cannot_answer", response_text=""),
        policy=TextOnlyPolicy(profile_root=profile_root, prompt_service=MaliciousPromptService()),
    )

    result = routing.handle_inbound(
        InboundMessage(channel="test", external_user_id="user", external_chat_id="chat", text="Нет данных")
    )

    assert result["route"]["outcome_kind"] == "cannot_answer"
    assert result["outcome"]["outcome_payload"]["response_text"] == "safe-simple-cannot-answer"


@pytest.mark.parametrize("text", ["", " [internal] ", "{internal}"])
def test_simple_successful_output_falls_back_to_simple_cannot_answer_for_internal_markers_brackets_or_empty_text(
    tmp_path: Path,
    text: str,
) -> None:
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'routing-simple-sanitize.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    engine = RecordingSimpleEngine(kind="grounded_answer", response_text=text)
    profile_root = tmp_path / "profile"
    profile_root.mkdir(parents=True, exist_ok=True)
    (profile_root / "profile.yaml").write_text(
        "response_templates:\n  simple_cannot_answer: safe-simple-cannot-answer\n",
        encoding="utf-8",
    )
    policy = TextOnlyPolicy(profile_root=profile_root, prompt_service=MaliciousPromptService())
    routing = RoutingService(
        session_factory=session_factory,
        answer_engine_mode="simple_full_corpus",
        simple_answer_engine=engine,
        policy=policy,
    )

    result = routing.handle_inbound(
        InboundMessage(channel="test", external_user_id="user", external_chat_id="chat", text="Сколько стоит?")
    )

    assert result["route"]["outcome_kind"] == "grounded_answer"
    assert result["outcome"]["outcome_payload"]["response_text"] == "safe-simple-cannot-answer"


def test_simple_policy_sanitizer_does_not_change_kind_or_outcome(tmp_path: Path) -> None:
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'routing-kind.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    routing = RoutingService(
        session_factory=session_factory,
        answer_engine_mode="simple_full_corpus",
        simple_answer_engine=RecordingSimpleEngine(kind="social_reply", response_text="hello"),
        policy=TextOnlyPolicy(),
    )

    result = routing.handle_inbound(
        InboundMessage(channel="test", external_user_id="user", external_chat_id="chat", text="Привет")
    )

    assert result["route"]["outcome_kind"] == "social_reply"
    assert result["outcome"]["outcome_type"] == "answer"
    assert result["outcome"]["outcome_payload"]["response_text"] == "Здравствуйте! hello"
