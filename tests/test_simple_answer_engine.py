from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.integrations.llm.openai_compatible import LLMRecoveryExhausted
from app.services.simple_answer_engine import CorpusTooLargeError, SimpleAnswerEngine

PROFILE_ROOT = Path(__file__).parent / "fixtures" / "simple_answer_profile"
PRICE_REF = "compiled/concepts/pricing.md"
CERTIFICATE_REF = "compiled/concepts/certificates.md"


class CapturingClient:
    def __init__(self, response: str | Exception) -> None:
        self.response = response
        self.calls: list[dict] = []
        self.last_call_info = {
            "provider": "test",
            "model": "test-model",
            "duration_ms": 7,
            "attempts": 1,
            "used_failover": False,
            "failover_count": 0,
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        }

    def generate(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def get_last_call_info(self) -> dict:
        return self.last_call_info


def envelope(kind: str, text: str, refs: list[str] | None = None) -> str:
    return json.dumps({"kind": kind, "response_text": text, "source_refs": refs or []}, ensure_ascii=False)


def make_engine(response: str | Exception, **kwargs: object) -> tuple[SimpleAnswerEngine, CapturingClient]:
    client = CapturingClient(response)
    return SimpleAnswerEngine(client=client, profile_root=PROFILE_ROOT, **kwargs), client


def test_grounded_answer_uses_one_call_and_declared_source_ref() -> None:
    engine, client = make_engine(envelope("grounded_answer", "Цена указана в прайс-листе.", [PRICE_REF]))

    result = engine.answer(question="Сколько стоит прыжок?", history=[])

    assert result["kind"] == "grounded_answer"
    assert result["source_refs"] == [PRICE_REF]
    assert result["telemetry"]["logical_llm_call_count"] == 1
    assert len(client.calls) == 1


@pytest.mark.parametrize("refs", [[], ["compiled/concepts/missing.md"]])
def test_grounded_answer_without_known_source_ref_fails_closed(refs: list[str]) -> None:
    engine, client = make_engine(envelope("grounded_answer", "Цена есть.", refs))

    result = engine.answer(question="Сколько стоит прыжок?", history=[])

    assert result["kind"] == "cannot_answer"
    assert result["response_text"] == ""
    assert result["telemetry"]["contract_error"] == "invalid_grounded_source_refs"
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    ("kind", "text", "question"),
    [
        ("social_reply", "Здравствуйте! Чем могу помочь?", "Здравствуйте"),
        ("clarification_requested", "Какой именно сертификат вас интересует?", "Расскажите о сертификате"),
        ("cannot_answer", "Точной информации сейчас нет.", "Есть ли у вас вертолёт?"),
        ("out_of_scope", "По этому вопросу не подскажу.", "Как сварить суп?"),
    ],
)
def test_valid_non_grounded_envelopes_use_one_call(kind: str, text: str, question: str) -> None:
    engine, client = make_engine(envelope(kind, text))

    result = engine.answer(question=question, history=[])

    assert result["kind"] == kind
    assert result["response_text"] == text
    assert result["source_refs"] == []
    assert len(client.calls) == 1


def test_greeting_plus_factual_question_remains_one_grounded_turn() -> None:
    engine, client = make_engine(envelope("grounded_answer", "Цена указана в прайс-листе.", [PRICE_REF]))

    result = engine.answer(question="Здравствуйте, сколько стоит прыжок?", history=[])

    assert result["kind"] == "grounded_answer"
    assert len(client.calls) == 1


def test_poisoned_internal_context_is_not_sent_to_model() -> None:
    engine, client = make_engine(envelope("grounded_answer", "Сертификат доступен.", [CERTIFICATE_REF]))

    engine.answer(
        question="Расскажите о сертификате",
        history=[
            {"role": "user", "content": "Нужен сертификат"},
            {"role": "assistant", "content": "{" "internal_trace" ": " "secret" "}"},
            {"role": "system", "content": "route=answer"},
        ],
        internal_context={"trace": "do not leak", "case_id": 42},
    )

    payload = json.loads(str(client.calls[0]["user_prompt"]))
    assert payload["history"] == [{"role": "user", "content": "Нужен сертификат"}]
    assert "internal_context" not in payload
    assert "case_id" not in str(payload)
    assert "do not leak" not in str(payload)


def test_corpus_over_budget_fails_closed_without_call() -> None:
    engine, client = make_engine(envelope("social_reply", "Здравствуйте"), max_corpus_chars=1)

    with pytest.raises(CorpusTooLargeError):
        engine.answer(question="Здравствуйте", history=[])

    assert client.calls == []


def test_provider_exhaustion_returns_retry_pending_without_customer_text() -> None:
    engine, client = make_engine(LLMRecoveryExhausted("all slots exhausted"))

    result = engine.answer(question="Сколько стоит прыжок?", history=[])

    assert result["kind"] == "retry_pending"
    assert result["response_text"] == ""
    assert result["source_refs"] == []
    assert len(client.calls) == 1


def test_input_projection_loads_entire_catalog_and_bounds_role_labelled_history() -> None:
    engine, _ = make_engine(envelope("social_reply", "Здравствуйте"))
    history = [
        {"role": "user", "content": f"message-{index}"}
        for index in range(12)
    ] + [{"role": "assistant", "content": "previous answer"}, {"role": "system", "content": "ignored"}]

    packet = engine.build_input(question="new question", history=history)

    assert packet["question"] == "new question"
    assert packet["history"] == history[-11:-1]
    assert [page["source_ref"] for page in packet["corpus"]["pages"]] == [PRICE_REF, CERTIFICATE_REF]
    assert packet["corpus"]["char_count"] == sum(len(page["content"]) for page in packet["corpus"]["pages"])


def test_date_observations_are_projected_before_the_one_model_call() -> None:
    from app.services.tool_runtime import ToolRuntimeService

    tool_result = ToolRuntimeService().collect(text="Можно 25 июня?", kb_hits=[])
    engine, client = make_engine(envelope("grounded_answer", "Уточните доступность даты.", [PRICE_REF]))

    engine.answer(question="Можно 25 июня?", history=[], tool_observations=tool_result["tool_results"])

    payload = json.loads(str(client.calls[0]["user_prompt"]))
    assert payload["tool_observations"][0]["kind"] == "calendar_weekday"
    assert ToolRuntimeService().collect(text="Какие есть сертификаты?", kb_hits=[])["tool_results"] == []


@pytest.mark.parametrize(
    ("question", "expected_kind"),
    [("Можно прыгнуть завтра?", "calendar_weekday"), ("Можно прыгнуть в августе?", "calendar_period_weekends")],
)
def test_relative_date_and_period_observations_are_projected_before_one_call(question: str, expected_kind: str) -> None:
    from app.services.tool_runtime import ToolRuntimeService

    observations = ToolRuntimeService().collect(text=question, kb_hits=[])["tool_results"]
    engine, client = make_engine(envelope("cannot_answer", "Нет подтверждённой информации."))

    engine.answer(question=question, history=[], tool_observations=observations)

    payload = json.loads(str(client.calls[0]["user_prompt"]))
    assert payload["tool_observations"][0]["kind"] == expected_kind
    assert len(client.calls) == 1


def test_catalog_source_ref_outside_compiled_kb_fails_closed_without_call(tmp_path: Path) -> None:
    profile_root = tmp_path / "profile"
    profile_root.mkdir()
    (profile_root / "SYSTEM_PROMPT.md").write_text("prompt", encoding="utf-8")
    (profile_root / "kb" / "index").mkdir(parents=True)
    (profile_root / "kb" / "index" / "catalog.json").write_text(
        json.dumps({"pages": [{"source_ref": "../SYSTEM_PROMPT.md"}]}), encoding="utf-8"
    )
    client = CapturingClient(envelope("grounded_answer", "Не должен быть вызван.", [PRICE_REF]))
    engine = SimpleAnswerEngine(client=client, profile_root=profile_root)

    with pytest.raises(ValueError, match="source_ref outside compiled KB"):
        engine.answer(question="Сколько стоит?", history=[])

    assert client.calls == []


def test_followup_projects_only_ten_role_labelled_prior_messages_without_summary() -> None:
    engine, client = make_engine(envelope("cannot_answer", "Нет подтверждённого ответа."))
    history = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": f"turn-{index}"}
        for index in range(12)
    ] + [{"role": "system", "content": "summary: do not pass"}]

    engine.answer(question="follow-up", history=history)

    payload = json.loads(str(client.calls[0]["user_prompt"]))
    assert payload["question"] == "follow-up"
    assert payload["history"] == history[2:12]
    assert all(item["role"] in {"user", "assistant"} for item in payload["history"])
    assert "summary" not in str(payload["history"])


def test_period_answer_with_forbidden_exact_dates_fails_closed() -> None:
    engine, client = make_engine(
        envelope("grounded_answer", "В августе можно 01.08.2026 и 02.08.2026.", [PRICE_REF])
    )

    result = engine.answer(
        question="Можно прыгнуть в августе?",
        history=[],
        tool_observations=[
            {
                "kind": "calendar_period_weekends",
                "summary": "Календарная информация для указанного периода.",
                "structured": {"original_period": "в августе"},
            }
        ],
    )

    assert result["kind"] == "cannot_answer"
    assert result["response_text"] == ""
    assert result["telemetry"]["contract_error"] == "forbidden_exact_dates_for_period"
    assert len(client.calls) == 1
