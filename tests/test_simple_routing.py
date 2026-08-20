from pathlib import Path

import pytest

from app.core.db import Base, make_session_factory
from app.schemas.message import InboundMessage
from app.services.routing import RoutingService
from app.workers.main import process_once


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


class RecordingAgentLoop:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls: list[dict] = []

    def run(self, *, text: str, context: dict) -> dict:
        self.calls.append({"text": text, "context": context})
        return self.result


class RecordingCatalogRetrieval:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def retrieve(self, query: str, knowledge_backend: str, knowledge_root: str, **kwargs: object) -> dict:
        self.calls.append(
            {
                "query": query,
                "knowledge_backend": knowledge_backend,
                "knowledge_root": knowledge_root,
                **kwargs,
            }
        )
        return {
            "kb_status": "found",
            "kb_mode": "llm_wiki_catalog",
            "kb_architecture": "llm_wiki",
            "navigation_mode": "llm",
            "coverage_review_mode": "llm",
            "extraction_mode": "grounded",
            "kb_snippets": [{"source_ref": "index/catalog.json", "retrieval_mode": "llm_wiki_catalog"}],
        }


class RecordingWikiReader:
    def __init__(
        self,
        *,
        grounding_status: str = "ready",
        missing_information: list[str] | None = None,
        needs_customer_clarification: bool = False,
    ) -> None:
        self.calls: list[dict] = []
        self.grounding_status = grounding_status
        self.missing_information = missing_information or []
        self.needs_customer_clarification = needs_customer_clarification

    def read(self, text: str, kb_hits: list[dict], **kwargs: object) -> dict:
        self.calls.append({"text": text, "kb_hits": kb_hits, **kwargs})
        return {
            "kb_status": "found",
            "kb_mode": "llm_wiki_selected_pages",
            "grounding_status": self.grounding_status,
            "answer_context": [{"source_ref": "compiled/concepts/booking.md", "text": "Тандем: форма записи."}],
            "grounded_facts": ["Запись на тандем доступна через форму."],
            "answer_basis": "Предложить форму записи на тандем.",
            "missing_information": self.missing_information,
            "needs_customer_clarification": self.needs_customer_clarification,
            "source_refs": ["compiled/concepts/booking.md"],
            "trace": {
                "kb_architecture": "llm_wiki",
                "navigation_mode": "llm",
                "coverage_review_mode": "llm",
                "extraction_mode": "grounded",
                "navigation": {"selected_source_refs": ["compiled/concepts/booking.md"]},
                "review": {"coverage_status": "enough"},
                "selected_source_refs": ["compiled/concepts/booking.md"],
            },
        }


class RecordingGroundedFinalizer:
    def __init__(
        self,
        *,
        route: str = "answer",
        response_text: str = "Здравствуйте! Заполните форму записи на тандем.",
    ) -> None:
        self.calls: list[dict] = []
        self.route = route
        self.response_text = response_text

    def respond(self, text: str, kb_result: dict, **kwargs: object) -> dict:
        self.calls.append({"text": text, "kb_result": kb_result, **kwargs})
        return {
            "route": self.route,
            "response_text": self.response_text,
            "confidence": 0.95,
            "reason": "ready_grounding",
            "llm_trace": [{"role": "direct_llm", "step": "final_response"}],
        }


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

    def render_clarification(self, question: str | None = None) -> str:
        return self._policy.render_clarification(question)


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


def test_agent_tool_loop_routes_social_reply_without_legacy_kb_dependencies(tmp_path: Path) -> None:
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'agent-loop.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    loop = RecordingAgentLoop(
        {
            "kb_result": {},
            "trace": {"actions": ["tool_not_used:unsupported_action"]},
            "llm_trace": [{"usage": {"total_tokens": 17}, "attempts": 2}],
            "tool_observations": [{"tool": "agent_action", "status": "not_used"}],
        }
    )
    finalizer = RecordingGroundedFinalizer(route="social_reply", response_text="Пожалуйста!")
    routing = RoutingService(
        session_factory=session_factory,
        answer_engine_mode="agent_tool_loop",
        agent_loop=loop,
        orchestrator=ForbiddenLegacyOwner(),
        kb_agent=ForbiddenLegacyDependency(),
        direct_llm=finalizer,
        retrieval=ForbiddenLegacyDependency(),
    )

    result = routing.handle_inbound(
        InboundMessage(channel="internal_test", external_user_id="igor", external_chat_id="igor", text="Спасибо")
    )

    assert result["route"]["answer_engine"] == "agent_tool_loop"
    assert result["route"]["route"] == "answer"
    assert result["outcome"]["outcome_payload"]["response_text"] == "Здравствуйте! Пожалуйста!"
    assert loop.calls[0]["text"] == "Спасибо"
    assert result["audit"]["agent_actions"] == ["tool_not_used:unsupported_action"]
    assert result["audit"]["logical_llm_call_count"] == 2
    assert result["audit"]["provider_attempt_count"] == 3
    assert finalizer.calls[0]["knowledge_mode"] == "prompt_only"
    assert finalizer.calls[0]["response_intent"] == "missing_grounding"


def test_agent_tool_loop_audits_actual_wiki_lookup_status(tmp_path: Path) -> None:
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'agent-loop-kb-status.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    loop = RecordingAgentLoop(
        {
            "kb_result": {"grounding_status": "not_found", "source_refs": []},
            "trace": {"actions": ["wiki_lookup"]},
            "tool_observations": [{"tool": "wiki_lookup", "status": "not_found", "source_refs": []}],
        }
    )
    finalizer = RecordingGroundedFinalizer(route="cannot_answer", response_text="Сейчас не могу дать точный ответ на этот вопрос.")
    routing = RoutingService(session_factory=session_factory, answer_engine_mode="agent_tool_loop", agent_loop=loop, direct_llm=finalizer)

    result = routing.handle_inbound(
        InboundMessage(channel="internal_test", external_user_id="igor", external_chat_id="igor", text="Какая гарантия?")
    )

    assert result["retrieval"]["kb_status"] == "not_found"
    assert result["audit"]["kb_status"] == "not_found"


def test_agent_tool_loop_wiki_outage_reaches_common_finalizer(tmp_path: Path) -> None:
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'agent-loop-outage.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    loop = RecordingAgentLoop(
        {
            "kb_result": {"grounding_status": "llm_unavailable", "source_refs": []},
            "trace": {"actions": ["wiki_lookup"]},
            "tool_observations": [{"tool": "wiki_lookup", "status": "llm_unavailable", "source_refs": [], "grounded_facts": []}],
        }
    )
    finalizer = RecordingGroundedFinalizer(route="cannot_answer", response_text="Уточню этот вопрос и вернусь с ответом.")
    routing = RoutingService(
        session_factory=session_factory,
        answer_engine_mode="agent_tool_loop",
        agent_loop=loop,
        direct_llm=finalizer,
    )

    result = routing.handle_inbound(
        InboundMessage(channel="telegram", external_user_id="igor", external_chat_id="igor", text="Вопрос")
    )

    assert finalizer.calls[0]["knowledge_mode"] == "prompt_only"
    assert finalizer.calls[0]["response_intent"] == "missing_grounding"
    assert result["route"]["route"] == "cannot_answer"
    assert result["outcome"]["outcome_payload"]["response_text"] == "Здравствуйте! Уточню этот вопрос и вернусь с ответом."
    assert result["audit"]["kb_status"] == "llm_unavailable"


def test_simple_llm_wiki_mode_uses_catalog_navigation_selected_pages_and_grounded_finalization(tmp_path: Path) -> None:
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'simple-llm-wiki.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    retrieval = RecordingCatalogRetrieval()
    wiki_reader = RecordingWikiReader()
    finalizer = RecordingGroundedFinalizer()
    routing = RoutingService(
        session_factory=session_factory,
        answer_engine_mode="simple_llm_wiki",
        knowledge_backend="filesystem",
        knowledge_root="/tmp/okf-wiki",
        retrieval=retrieval,
        kb_agent=wiki_reader,
        direct_llm=finalizer,
        orchestrator=ForbiddenLegacyOwner(),
        summary_service=ForbiddenLegacyDependency(),
    )

    result = routing.handle_inbound(
        InboundMessage(
            channel="telegram",
            external_user_id="igor",
            external_chat_id="igor",
            text="Привет, звоню по телефону никто не берет трубку. Хотели бы прыгнуть в тандеме.",
        )
    )

    assert result["route"]["route"] == "answer"
    assert result["route"]["answer_engine"] == "simple_llm_wiki"
    assert result["outcome"]["outcome_payload"]["response_text"].startswith("Здравствуйте!")
    assert retrieval.calls[0]["knowledge_root"] == "/tmp/okf-wiki"
    assert wiki_reader.calls[0]["kb_hits"][0]["retrieval_mode"] == "llm_wiki_catalog"
    assert wiki_reader.calls[0]["require_coverage_review"] is True
    assert finalizer.calls[0]["kb_result"]["kb_mode"] == "llm_wiki_selected_pages"
    assert result["audit"]["kb_architecture"] == "llm_wiki"
    assert result["audit"]["navigation_mode"] == "llm"
    assert result["audit"]["coverage_review_mode"] == "llm"
    assert result["audit"]["selected_source_refs"] == ["compiled/concepts/booking.md"]


def test_simple_llm_wiki_first_reply_with_selected_pages_asks_clarification_instead_of_refusal(tmp_path: Path) -> None:
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'simple-llm-wiki-no-answer.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    finalizer = RecordingGroundedFinalizer(
        route="clarification_requested",
        response_text="Здравствуйте! Какая услуга вас заинтересовала?",
    )
    routing = RoutingService(
        session_factory=session_factory,
        answer_engine_mode="simple_llm_wiki",
        knowledge_backend="filesystem",
        knowledge_root="/tmp/okf-wiki",
        retrieval=RecordingCatalogRetrieval(),
        kb_agent=RecordingWikiReader(
            grounding_status="not_found",
            missing_information=["какая именно услуга интересует"],
            needs_customer_clarification=True,
        ),
        direct_llm=finalizer,
        orchestrator=ForbiddenLegacyOwner(),
        summary_service=ForbiddenLegacyDependency(),
        policy=TextOnlyPolicy(),
    )

    result = routing.handle_inbound(
        InboundMessage(channel="telegram", external_user_id="igor", external_chat_id="igor", text="Здравствуйте! Меня заинтересовала эта услуга.")
    )

    assert result["route"]["route"] == "clarification_requested"
    assert result["outcome"]["outcome_payload"]["response_text"] == "Здравствуйте! Какая услуга вас заинтересовала?"
    assert len(finalizer.calls) == 1
    assert finalizer.calls[0]["text"] == "Здравствуйте! Меня заинтересовала эта услуга."
    assert finalizer.calls[0]["knowledge_mode"] == "prompt_only"
    assert finalizer.calls[0]["response_intent"] == "clarification"
    assert finalizer.calls[0]["kb_result"]["grounded_facts"] == []


def test_simple_llm_wiki_selected_pages_without_missing_customer_detail_stays_cannot_answer(tmp_path: Path) -> None:
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'simple-llm-wiki-selected-but-not-ambiguous.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    routing = RoutingService(
        session_factory=session_factory,
        answer_engine_mode="simple_llm_wiki",
        knowledge_backend="filesystem",
        knowledge_root="/tmp/okf-wiki",
        retrieval=RecordingCatalogRetrieval(),
        kb_agent=RecordingWikiReader(grounding_status="not_found"),
        direct_llm=RecordingGroundedFinalizer(
            route="cannot_answer",
            response_text="Сейчас не могу дать точный ответ на этот вопрос.",
        ),
        orchestrator=ForbiddenLegacyOwner(),
        summary_service=ForbiddenLegacyDependency(),
        policy=TextOnlyPolicy(),
    )

    result = routing.handle_inbound(
        InboundMessage(channel="telegram", external_user_id="igor", external_chat_id="igor", text="Какая гарантия на неизвестное оборудование?")
    )

    assert result["route"]["route"] == "cannot_answer"


def test_simple_llm_wiki_first_reply_without_grounding_uses_common_finalizer(tmp_path: Path) -> None:
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'simple-llm-wiki-first-missing-grounding.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    finalizer = RecordingGroundedFinalizer(
        route="clarification_requested",
        response_text="Здравствуйте! Какая услуга вас заинтересовала?",
    )
    routing = RoutingService(
        session_factory=session_factory,
        answer_engine_mode="simple_llm_wiki",
        knowledge_backend="filesystem",
        knowledge_root="/tmp/okf-wiki",
        retrieval=RecordingCatalogRetrieval(),
        kb_agent=RecordingWikiReader(grounding_status="not_found"),
        direct_llm=finalizer,
        orchestrator=ForbiddenLegacyOwner(),
        summary_service=ForbiddenLegacyDependency(),
        policy=TextOnlyPolicy(),
    )

    result = routing.handle_inbound(
        InboundMessage(channel="telegram", external_user_id="igor", external_chat_id="igor", text="Здравствуйте! Меня заинтересовала эта услуга.")
    )

    assert result["route"]["route"] == "clarification_requested"
    assert result["outcome"]["outcome_payload"]["response_text"] == "Здравствуйте! Какая услуга вас заинтересовала?"
    assert len(finalizer.calls) == 1
    assert finalizer.calls[0]["knowledge_mode"] == "prompt_only"
    assert finalizer.calls[0]["response_intent"] == "missing_grounding"


def test_simple_llm_wiki_follow_up_without_facts_still_uses_finalizer(tmp_path: Path) -> None:
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'simple-llm-wiki-follow-up-no-facts.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    finalizer = RecordingGroundedFinalizer(
        route="answer",
        response_text="Пожалуйста! Если появятся вопросы, напишите.",
    )
    routing = RoutingService(
        session_factory=session_factory,
        answer_engine_mode="simple_llm_wiki",
        knowledge_backend="filesystem",
        knowledge_root="/tmp/okf-wiki",
        retrieval=RecordingCatalogRetrieval(),
        kb_agent=RecordingWikiReader(grounding_status="not_found"),
        direct_llm=finalizer,
        orchestrator=ForbiddenLegacyOwner(),
        summary_service=ForbiddenLegacyDependency(),
        policy=TextOnlyPolicy(),
    )
    first = InboundMessage(channel="telegram", external_user_id="igor", external_chat_id="igor", text="Спасибо")
    second = InboundMessage(channel="telegram", external_user_id="igor", external_chat_id="igor", text="Я поняла")

    first_result = routing.handle_inbound(first)
    routing.record_outbound_message(
        first_result["case"]["case_id"],
        first_result["outcome"]["outcome_payload"]["response_text"],
    )
    result = routing.handle_inbound(second)

    assert result["route"]["route"] == "answer"
    assert result["outcome"]["outcome_payload"]["response_text"] == "Пожалуйста! Если появятся вопросы, напишите."
    assert len(finalizer.calls) == 2
    assert finalizer.calls[-1]["knowledge_mode"] == "prompt_only"
    assert finalizer.calls[-1]["response_intent"] == "missing_grounding"


def test_simple_llm_wiki_missing_kb_fact_without_explicit_ambiguity_stays_cannot_answer(tmp_path: Path) -> None:
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'simple-llm-wiki-missing-fact.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    routing = RoutingService(
        session_factory=session_factory,
        answer_engine_mode="simple_llm_wiki",
        knowledge_backend="filesystem",
        knowledge_root="/tmp/okf-wiki",
        retrieval=RecordingCatalogRetrieval(),
        kb_agent=RecordingWikiReader(grounding_status="not_found", missing_information=["гарантийный срок"]),
        direct_llm=RecordingGroundedFinalizer(
            route="cannot_answer",
            response_text="Сейчас не могу дать точный ответ на этот вопрос.",
        ),
        orchestrator=ForbiddenLegacyOwner(),
        summary_service=ForbiddenLegacyDependency(),
        policy=TextOnlyPolicy(),
    )

    result = routing.handle_inbound(
        InboundMessage(channel="telegram", external_user_id="igor", external_chat_id="igor", text="Какая гарантия на оборудование?")
    )

    assert result["route"]["route"] == "cannot_answer"


def test_simple_llm_wiki_fails_closed_when_retrieval_does_not_return_okf_catalog(tmp_path: Path) -> None:
    class LegacyRetrieval:
        def retrieve(self, *args: object, **kwargs: object) -> dict:
            return {
                "kb_status": "found",
                "kb_mode": "retrieved_snippets",
                "kb_snippets": [{"text": "legacy snippet"}],
            }

    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'simple-llm-wiki-invalid-kb.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    finalizer = RecordingGroundedFinalizer()
    routing = RoutingService(
        session_factory=session_factory,
        answer_engine_mode="simple_llm_wiki",
        knowledge_backend="filesystem",
        knowledge_root="/tmp/not-okf",
        retrieval=LegacyRetrieval(),
        kb_agent=ForbiddenLegacyDependency(),
        direct_llm=finalizer,
        policy=TextOnlyPolicy(),
    )

    result = routing.handle_inbound(
        InboundMessage(channel="telegram", external_user_id="igor", external_chat_id="igor", text="Как записаться?")
    )

    assert result["route"]["route"] == "cannot_answer"
    assert result["kb_result"]["trace"]["reason"] == "simple_llm_wiki_requires_okf_catalog"
    assert finalizer.calls == []


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


class TraceRecordingEngine(RecordingSimpleEngine):
    def __init__(self, *, kind: str = "grounded_answer", response_text: str = "Подтверждённый ответ.") -> None:
        super().__init__(kind=kind, response_text=response_text)

    def answer(self, **kwargs: object) -> dict:
        result = super().answer(**kwargs)
        result["telemetry"] = {
            "answer_engine": "simple_full_corpus",
            "logical_llm_call_count": 2,
            "provider_attempt_count": 3,
        }
        return result


def test_simple_trace_persists_route_observability_fields(tmp_path: Path) -> None:
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'routing-trace.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    routing = RoutingService(
        session_factory=session_factory,
        answer_engine_mode="simple_full_corpus",
        simple_answer_engine=TraceRecordingEngine(),
    )

    result = routing.handle_inbound(
        InboundMessage(channel="test", external_user_id="user", external_chat_id="chat", text="Сколько стоит?")
    )

    assert result["route"]["outcome_kind"] == "grounded_answer"
    assert result["route"]["source_refs"] == ["compiled/concepts/pricing.md"]
    assert result["audit"]["outcome_kind"] == "grounded_answer"
    assert result["audit"]["source_refs"] == ["compiled/concepts/pricing.md"]
    assert result["audit"]["logical_llm_call_count"] == 2
    assert result["audit"]["provider_attempt_count"] == 3


def test_worker_process_once_invokes_due_retry_without_consuming_fake_gateway_state() -> None:
    class FakeGateway:
        def __init__(self) -> None:
            self.processed = 0
            self.handled: list[dict] = []

        def process_due_retries(self) -> int:
            self.processed += 1
            return 1

        def handle_update(self, update: dict) -> None:
            self.handled.append(update)

    class FakePoller:
        def get_updates(self, offset=None, timeout=None):
            return {"result": []}

    class FakeAcker:
        def __init__(self) -> None:
            self.offsets: list[int] = []

        def ack(self, offset: int) -> None:
            self.offsets.append(offset)

    result = process_once(FakeGateway(), FakePoller(), FakeAcker(), offset=10)

    assert result["retry_processed"] == 1
    assert result["processed"] == 0
