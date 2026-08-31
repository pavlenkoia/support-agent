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


def test_agent_tool_loop_routes_social_reply_without_legacy_kb_dependencies(tmp_path: Path) -> None:
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'agent-loop.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    loop = RecordingAgentLoop(
        {
            "kb_result": {},
            "final_result": {
                "route": "social_reply",
                "response_text": "Пожалуйста!",
                "confidence": 1.0,
                "reason": "social_reply",
            },
            "trace": {"actions": ["final_response"]},
            "llm_trace": [{"usage": {"total_tokens": 17}, "attempts": 2}],
            "tool_observations": [],
        }
    )
    finalizer = RecordingGroundedFinalizer(route="social_reply", response_text="Пожалуйста!")
    routing = RoutingService(
        session_factory=session_factory,
        answer_engine_mode="agent_tool_loop",
        turn_service=loop,
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
    assert result["audit"]["agent_actions"] == ["final_response"]
    assert result["audit"]["logical_llm_call_count"] == 1
    assert result["audit"]["provider_attempt_count"] == 2
    assert finalizer.calls == []


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
    routing = RoutingService(session_factory=session_factory, answer_engine_mode="agent_tool_loop", turn_service=loop, direct_llm=finalizer)

    result = routing.handle_inbound(
        InboundMessage(channel="internal_test", external_user_id="igor", external_chat_id="igor", text="Какая гарантия?")
    )

    assert result["retrieval"]["kb_status"] == "not_found"
    assert result["audit"]["kb_status"] == "not_found"


def test_agent_tool_loop_never_passes_nonready_extraction_to_finalizer(tmp_path: Path) -> None:
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'agent-loop-extracted-facts.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    facts = ["Условия записи на дату указаны в закреплённом посте или анонсе на эту дату."]
    loop = RecordingAgentLoop(
        {
            "kb_result": {
                "grounding_status": "not_found",
                "answer_basis": facts[0],
                "grounded_facts": facts,
                "source_refs": ["compiled/concepts/booking.md"],
            },
            "trace": {"actions": ["wiki_lookup"]},
            "tool_observations": [{"tool": "wiki_lookup", "status": "not_found", "grounded_facts": facts}],
        }
    )
    finalizer = RecordingGroundedFinalizer(response_text="Условия записи указаны в закреплённом посте на эту дату.")
    routing = RoutingService(session_factory=session_factory, answer_engine_mode="agent_tool_loop", turn_service=loop, direct_llm=finalizer)

    routing.handle_inbound(
        InboundMessage(channel="internal_test", external_user_id="igor", external_chat_id="igor", text="Записаться можно на 29?")
    )

    assert finalizer.calls[0]["knowledge_mode"] == "prompt_only"
    assert finalizer.calls[0]["response_intent"] == "missing_grounding"
    assert finalizer.calls[0]["kb_result"] == {
        "grounding_status": "not_found",
        "answer_basis": "",
        "grounded_facts": [],
    }


def test_agent_tool_loop_wiki_outage_schedules_retry_without_customer_text(tmp_path: Path) -> None:
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
        turn_service=loop,
        direct_llm=finalizer,
    )

    result = routing.handle_inbound(
        InboundMessage(channel="telegram", external_user_id="igor", external_chat_id="igor", text="Вопрос")
    )

    assert finalizer.calls == []
    assert result["route"]["route"] == "retry_pending"
    assert result["outcome"]["outcome_payload"]["response_text"] == ""
    assert result["audit"]["kb_status"] == "llm_unavailable"




















@pytest.mark.parametrize("text", ["", " [internal] ", "{internal}"])




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
