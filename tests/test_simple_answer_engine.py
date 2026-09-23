from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.integrations.llm.openai_compatible import LLMRecoveryExhausted
from app.services.simple_answer_engine import CorpusTooLargeError, SimpleAnswerEngine

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "simple_answer_profile"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PRICE_REF = "price.basic"
CERTIFICATE_REF = "certificate.website"
PRICE_PAGE_PATH = "compiled/concepts/pricing.md"
CERTIFICATE_PAGE_PATH = "compiled/concepts/certificates.md"
PRICE_PAGE = "Цена прыжка на 10 прыжков — 12 000 ₽."
CERTIFICATE_PAGE = "Сертификат можно оформить на сайте."


def make_profile_root(tmp_path: Path) -> Path:
    profile_root = tmp_path / "profile"
    (profile_root / "kb" / "index").mkdir(parents=True)
    (profile_root / "kb" / PRICE_PAGE_PATH).parent.mkdir(parents=True, exist_ok=True)
    (profile_root / "kb" / PRICE_PAGE_PATH).write_text(PRICE_PAGE, encoding="utf-8")
    (profile_root / "kb" / CERTIFICATE_PAGE_PATH).write_text(CERTIFICATE_PAGE, encoding="utf-8")
    (profile_root / "kb" / "index" / "catalog.json").write_text(
        json.dumps(
            {
                "pages": [
                    {"source_ref": PRICE_PAGE_PATH, "title": "Pricing", "summary": "Prices"},
                    {"source_ref": CERTIFICATE_PAGE_PATH, "title": "Certificates", "summary": "Certificates"},
                ]
            }
        ),
        encoding="utf-8",
    )
    write_runtime_artifact(profile_root)
    (profile_root / "SYSTEM_PROMPT.md").write_text(FIXTURE_ROOT.joinpath("SYSTEM_PROMPT.md").read_text(encoding="utf-8"), encoding="utf-8")
    return profile_root


def write_runtime_artifact(profile_root: Path, *, price_text: str = PRICE_PAGE, certificate_text: str = CERTIFICATE_PAGE) -> None:
    facts = [
        {"id": PRICE_REF, "text": price_text, "conditions": [], "source_refs": ["price:01"]},
        {"id": CERTIFICATE_REF, "text": certificate_text, "conditions": [], "source_refs": ["certificate:01"]},
    ]
    artifact = {"schema_version": 1, "facts": facts}
    (profile_root / "kb" / "knowledge-base.v1.json").write_text(json.dumps(artifact), encoding="utf-8")


def test_simple_answer_engine_sends_only_runtime_knowledge_artifact(tmp_path: Path) -> None:
    profile_root = make_profile_root(tmp_path)
    artifact = {
        "schema_version": 1,
        "facts": [{"id": "price.basic", "text": PRICE_PAGE, "conditions": [], "source_refs": ["price:01"]}],
    }
    (profile_root / "kb" / "knowledge-base.v1.json").write_text(json.dumps(artifact), encoding="utf-8")
    engine, client = make_engine(envelope("social_reply", "Спасибо!"), profile_root=profile_root)

    engine.answer(question="Привет", history=[])

    payload = json.loads(str(client.calls[0]["user_prompt"]))
    assert payload["knowledge_base"] == artifact
    assert "compiled_corpus" not in payload


def test_simple_answer_engine_requires_generated_runtime_knowledge_artifact(tmp_path: Path) -> None:
    profile_root = make_profile_root(tmp_path)
    (profile_root / "kb" / "knowledge-base.v1.json").unlink()
    engine, client = make_engine(envelope("social_reply", "Спасибо!"), profile_root=profile_root)

    with pytest.raises(ValueError, match="runtime knowledge artifact"):
        engine.answer(question="Привет", history=[])

    assert client.calls == []


def test_grounded_evidence_rejects_legacy_page_corpus(tmp_path: Path) -> None:
    engine, _ = make_engine(envelope("grounded_answer", "ignored"), profile_root=make_profile_root(tmp_path))

    with pytest.raises(ValueError, match="invalid runtime knowledge facts"):
        engine._finalize_grounded_answer(
            {"evidence": [{"fact_id": PRICE_REF, "quote": PRICE_PAGE}]},
            {"corpus": {"pages": [{"source_ref": PRICE_REF, "content": PRICE_PAGE}]}},
            "Цена указана.",
        )


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


def envelope(kind: str, text: str, refs: list[str] | None = None, evidence: list[dict[str, Any]] | None = None) -> str:
    payload: dict[str, object] = {"kind": kind, "response_text": text, "source_refs": refs or []}
    if evidence is not None:
        payload["evidence"] = evidence
    return json.dumps(payload, ensure_ascii=False)


def make_engine(
    response: str | Exception,
    *,
    profile_root: Path,
    **kwargs: object,
) -> tuple[SimpleAnswerEngine, CapturingClient]:
    client = CapturingClient(response)
    return SimpleAnswerEngine(client=client, profile_root=profile_root, **kwargs), client


def test_grounded_answer_uses_one_call_and_declared_source_ref(tmp_path: Path) -> None:
    engine, client = make_engine(
        envelope(
            "grounded_answer",
            "Цена указана в прайс-листе.",
            [PRICE_REF],
            [{"source_ref": PRICE_REF, "quote": "Цена прыжка на 10 прыжков — 12 000 ₽."}],
        ),
        profile_root=make_profile_root(tmp_path),
    )

    result = engine.answer(question="Сколько стоит прыжок?", history=[])

    assert result["kind"] == "grounded_answer"
    assert result["source_refs"] == [PRICE_REF]
    assert result["response_text"] == "Цена прыжка на 10 прыжков — 12 000 ₽."
    assert result["telemetry"]["logical_llm_call_count"] == 1
    assert len(client.calls) == 1


def test_natural_full_corpus_preserves_grounded_customer_text_after_evidence_validation(tmp_path: Path) -> None:
    natural_text = "На тандем можно оформить подарочный сертификат на сайте."
    engine, client = make_engine(
        envelope(
            "grounded_answer",
            natural_text,
            [CERTIFICATE_REF],
            [{"source_ref": CERTIFICATE_REF, "quote": "Сертификат можно оформить на сайте."}],
        ),
        profile_root=make_profile_root(tmp_path),
        preserve_grounded_text=True,
    )

    result = engine.answer(question="Как оформить сертификат?", history=[])

    assert result["kind"] == "grounded_answer"
    assert result["response_text"] == natural_text
    assert result["source_refs"] == [CERTIFICATE_REF]
    assert result["telemetry"]["logical_llm_call_count"] == 1
    assert len(client.calls) == 1


def test_natural_full_corpus_accepts_evidence_with_only_markdown_presentation_difference(tmp_path: Path) -> None:
    profile_root = make_profile_root(tmp_path)
    source_text = "Онлайн-покупка доступна на `https://example.test`; электронный сертификат приходит на почту.\n- Бумажный сертификат можно получить в офисе."
    (profile_root / "kb" / CERTIFICATE_PAGE_PATH).write_text(source_text, encoding="utf-8")
    write_runtime_artifact(profile_root, certificate_text=source_text)
    natural_text = "Сертификат можно купить онлайн; он придёт на почту."
    engine, _ = make_engine(
        envelope(
            "grounded_answer",
            natural_text,
            [CERTIFICATE_REF],
            [{"source_ref": CERTIFICATE_REF, "quote": "Онлайн-покупка доступна на https://example.test; электронный сертификат приходит на почту. Бумажный сертификат можно получить в офисе."}],
        ),
        profile_root=profile_root,
        preserve_grounded_text=True,
    )

    result = engine.answer(question="Можно купить онлайн?", history=[])

    assert result["kind"] == "grounded_answer"
    assert result["response_text"] == natural_text
    assert result["source_refs"] == [CERTIFICATE_REF]


def test_natural_full_corpus_prompt_requires_natural_text_with_exact_evidence(tmp_path: Path) -> None:
    engine, client = make_engine(
        envelope(
            "grounded_answer",
            "На тандем можно оформить подарочный сертификат на сайте.",
            [CERTIFICATE_REF],
            [{"source_ref": CERTIFICATE_REF, "quote": "Сертификат можно оформить на сайте."}],
        ),
        profile_root=make_profile_root(tmp_path),
        preserve_grounded_text=True,
    )

    engine.answer(question="Как оформить сертификат?", history=[])

    contract = json.loads(str(client.calls[0]["user_prompt"]))["output_contract"]
    assert contract["response_text"] == "non-empty natural customer response"
    assert contract["rule"] == (
        "Верни один короткий ответ клиенту. Если последнее сообщение прямо выражает благодарность и не содержит вопроса "
        "или просьбы сообщить сведения, ответь ровно: «Пожалуйста!». "
        "Не используй для такого ответа базу знаний или историю и не повторяй прошлый ответ. "
        "Во всех остальных случаях отвечай только по фактам из базы, которые прямо относятся к последнему сообщению. "
        "Не задавай лишних вопросов, не предлагай следующих шагов и не добавляй рекламу."
    )


def test_natural_full_corpus_uses_its_own_customer_prompt(tmp_path: Path) -> None:
    profile_root = make_profile_root(tmp_path)
    (profile_root / "SIMPLE_ANSWER_PROMPT.md").write_text(
        "Отвечай клиенту коротко и только по буквальному вопросу.", encoding="utf-8"
    )
    engine, client = make_engine(
        envelope(
            "grounded_answer",
            "Сертификат можно оформить на сайте.",
            [CERTIFICATE_REF],
            [{"source_ref": CERTIFICATE_REF, "quote": "Сертификат можно оформить на сайте."}],
        ),
        profile_root=profile_root,
        preserve_grounded_text=True,
    )

    engine.answer(question="Как оформить сертификат?", history=[])

    assert client.calls[0]["system_prompt"] == "Отвечай клиенту коротко и только по буквальному вопросу."


def test_production_customer_prompt_prohibits_unsolicited_questions_and_evaluations() -> None:
    source_prompt = (REPOSITORY_ROOT / "deploy" / "profile-source" / "SIMPLE_ANSWER_PROMPT.md").read_text(encoding="utf-8")
    runtime_prompt = (REPOSITORY_ROOT / "deploy" / "profile" / "SIMPLE_ANSWER_PROMPT.md").read_text(encoding="utf-8")

    required_contract = (
        "Не добавляй даже подтверждённые сведения, если клиент о них не спрашивал.",
        "Если последнее сообщение прямо выражает благодарность и не содержит вопроса или просьбы сообщить сведения, ответь ровно: «Пожалуйста!».",
        "Не используй для такого ответа базу знаний или историю и не повторяй прошлый ответ.",
        "Во всех остальных случаях отвечай только по фактам из базы, которые прямо относятся к последнему сообщению.",
        "Не задавай вопросов, не запрашивай дату или другие детали, не предлагай действий и не веди диалог дальше.",
    )
    for clause in required_contract:
        assert clause in source_prompt
        assert clause in runtime_prompt


def test_user_prompt_contract_includes_evidence_shape_and_exact_quote_requirement(tmp_path: Path) -> None:
    engine, client = make_engine(
        envelope(
            "grounded_answer",
            "Цена указана в прайс-листе.",
            [PRICE_REF],
            [{"source_ref": PRICE_REF, "quote": "Цена прыжка на 10 прыжков — 12 000 ₽."}],
        ),
        profile_root=make_profile_root(tmp_path),
    )

    engine.answer(question="Сколько стоит прыжок?", history=[])

    payload = json.loads(str(client.calls[0]["user_prompt"]))
    assert payload["output_contract"]["evidence"] == [{"fact_id": "fact ID", "quote": "exact fact text substring"}]
    assert "exact source substring" in str(payload["output_contract"])


def test_grounded_answer_requires_exact_extracts_and_ignores_model_factual_prose(tmp_path: Path) -> None:
    assert "Ночные прыжки не проводятся." not in PRICE_PAGE
    engine, client = make_engine(
        envelope(
            "grounded_answer",
            "Ночные прыжки не проводятся.",
            [PRICE_REF],
            [{"source_ref": PRICE_REF, "quote": "Цена прыжка на 10 прыжков — 12 000 ₽."}],
        ),
        profile_root=make_profile_root(tmp_path),
    )

    result = engine.answer(question="Есть ли ночные прыжки?", history=[])

    assert result["kind"] == "grounded_answer"
    assert result["response_text"] == "Цена прыжка на 10 прыжков — 12 000 ₽."
    assert "Ночные прыжки не проводятся." not in result["response_text"]
    assert len(client.calls) == 1


def test_grounded_answer_rejects_missing_empty_and_too_many_evidence_items(tmp_path: Path) -> None:
    profile_root = make_profile_root(tmp_path)
    cases = [
        (None, "invalid_evidence_count"),
        ([], "invalid_evidence_count"),
        ([{"source_ref": PRICE_REF, "quote": ""}], "invalid_evidence_quote"),
        ([{"source_ref": PRICE_REF, "quote": "Ночные прыжки проводятся."}], "invalid_evidence_quote"),
        (
            [
                {"source_ref": PRICE_REF, "quote": "Цена прыжка на 10 прыжков — 12 000 ₽."},
                {"source_ref": CERTIFICATE_REF, "quote": "Сертификат можно оформить на сайте."},
                {"source_ref": PRICE_REF, "quote": "Цена прыжка на 10 прыжков — 12 000 ₽."},
                {"source_ref": CERTIFICATE_REF, "quote": "Сертификат можно оформить на сайте."},
            ],
            "invalid_evidence_count",
        ),
    ]

    for evidence, expected_error in cases:
        engine, client = make_engine(envelope("grounded_answer", "ignored", [PRICE_REF], evidence), profile_root=profile_root)
        result = engine.answer(question="Сколько стоит прыжок?", history=[])
        assert result["kind"] == "cannot_answer"
        assert result["telemetry"]["contract_error"] == expected_error
        assert len(client.calls) == 1


def test_grounded_answer_derives_source_refs_from_valid_evidence(tmp_path: Path) -> None:
    profile_root = make_profile_root(tmp_path)
    engine, client = make_engine(
        envelope(
            "grounded_answer",
            "Цена указана в прайс-листе.",
            [PRICE_REF],
            [
                {"source_ref": CERTIFICATE_REF, "quote": "Сертификат можно оформить на сайте."},
                {"source_ref": PRICE_REF, "quote": "Цена прыжка на 10 прыжков — 12 000 ₽."},
                {"source_ref": PRICE_REF, "quote": "Цена прыжка на 10 прыжков — 12 000 ₽."},
            ],
        ),
        profile_root=profile_root,
    )

    result = engine.answer(question="Что есть?", history=[])

    assert result["kind"] == "grounded_answer"
    assert result["response_text"] == "Сертификат можно оформить на сайте. Цена прыжка на 10 прыжков — 12 000 ₽."
    assert result["source_refs"] == [CERTIFICATE_REF, PRICE_REF]
    assert len(client.calls) == 1


def test_grounded_answer_deduplicates_quotes_and_preserves_first_seen_order(tmp_path: Path) -> None:
    profile_root = make_profile_root(tmp_path)
    engine, client = make_engine(
        envelope(
            "grounded_answer",
            "",
            [PRICE_REF, CERTIFICATE_REF],
            [
                {"source_ref": CERTIFICATE_REF, "quote": CERTIFICATE_PAGE},
                {"source_ref": PRICE_REF, "quote": PRICE_PAGE},
                {"source_ref": PRICE_REF, "quote": PRICE_PAGE},
            ],
        ),
        profile_root=profile_root,
    )

    result = engine.answer(question="Что известно?", history=[])

    assert result["kind"] == "grounded_answer"
    assert result["response_text"] == f"{CERTIFICATE_PAGE} {PRICE_PAGE}"
    assert result["source_refs"] == [CERTIFICATE_REF, PRICE_REF]
    assert len(client.calls) == 1


def test_grounded_answer_rejects_non_string_model_response_text(tmp_path: Path) -> None:
    payload = {
        "kind": "grounded_answer",
        "response_text": 12_000,
        "source_refs": [PRICE_REF],
        "evidence": [{"source_ref": PRICE_REF, "quote": PRICE_PAGE}],
    }
    engine, client = make_engine(
        json.dumps(payload, ensure_ascii=False),
        profile_root=make_profile_root(tmp_path),
    )

    result = engine.answer(question="Сколько стоит прыжок?", history=[])

    assert result["kind"] == "cannot_answer"
    assert result["telemetry"]["contract_error"] == "invalid_required_fields"
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "evidence",
    [
        [{"source_ref": 1, "quote": PRICE_PAGE}],
        [{"source_ref": PRICE_REF, "quote": 12_000}],
    ],
)
def test_grounded_answer_rejects_non_string_evidence_fields(
    tmp_path: Path,
    evidence: list[dict[str, object]],
) -> None:
    engine, client = make_engine(
        envelope("grounded_answer", "ignored", [PRICE_REF], evidence),
        profile_root=make_profile_root(tmp_path),
    )

    result = engine.answer(question="Сколько стоит прыжок?", history=[])

    assert result["kind"] == "cannot_answer"
    assert result["telemetry"]["contract_error"] == "invalid_evidence_types"
    assert len(client.calls) == 1


def test_non_grounded_kinds_reject_any_evidence(tmp_path: Path) -> None:
    profile_root = make_profile_root(tmp_path)
    for kind in ["social_reply", "clarification_requested", "cannot_answer", "out_of_scope"]:
        engine, client = make_engine(
            envelope(
                kind,
                "ok",
                [],
                [{"source_ref": PRICE_REF, "quote": "Цена прыжка на 10 прыжков — 12 000 ₽."}],
            ),
            profile_root=profile_root,
        )
        result = engine.answer(question="question", history=[])
        assert result["kind"] == "cannot_answer"
        assert result["telemetry"]["contract_error"] == "unexpected_evidence"
        assert len(client.calls) == 1


@pytest.mark.parametrize("refs", [[], ["compiled/concepts/missing.md"]])
def test_grounded_answer_without_known_source_ref_fails_closed(tmp_path: Path, refs: list[str]) -> None:
    evidence = [{"source_ref": ref, "quote": "Цена прыжка на 10 прыжков — 12 000 ₽."} for ref in refs] or [{"source_ref": PRICE_REF, "quote": ""}]
    engine, client = make_engine(
        envelope("grounded_answer", "Цена есть.", [PRICE_REF], evidence),
        profile_root=make_profile_root(tmp_path),
    )

    result = engine.answer(question="Сколько стоит прыжок?", history=[])

    assert result["kind"] == "cannot_answer"
    assert result["response_text"] == ""
    assert result["telemetry"]["contract_error"] in {"invalid_evidence_count", "invalid_evidence_source_ref", "invalid_evidence_quote"}
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
def test_valid_non_grounded_envelopes_use_one_call(tmp_path: Path, kind: str, text: str, question: str) -> None:
    engine, client = make_engine(envelope(kind, text), profile_root=make_profile_root(tmp_path))

    result = engine.answer(question=question, history=[])

    assert result["kind"] == kind
    assert result["response_text"] == text
    assert result["source_refs"] == []
    assert len(client.calls) == 1


def test_plain_text_provider_fallback_is_explicitly_tagged(tmp_path: Path) -> None:
    engine, client = make_engine(
        "Короткий ответ.",
        profile_root=make_profile_root(tmp_path),
        preserve_grounded_text=True,
    )

    result = engine.answer(question="Вопрос", history=[])

    assert result["kind"] == "final_response"
    assert result["response_text"] == "Короткий ответ."
    assert result["telemetry"]["contract_error"] == "provider_plain_text"
    assert len(client.calls) == 1


def test_final_response_without_classification_is_a_natural_dialogue_reply(tmp_path: Path) -> None:
    engine, client = make_engine(
        json.dumps({"response_text": "Пожалуйста! Обращайтесь, если появятся вопросы.", "source_refs": [], "evidence": []}, ensure_ascii=False),
        profile_root=make_profile_root(tmp_path),
        preserve_grounded_text=True,
    )

    result = engine.answer(question="Спасибо, я поняла.", history=[])

    assert result["kind"] == "final_response"
    assert result["response_text"] == "Пожалуйста! Обращайтесь, если появятся вопросы."
    assert result["source_refs"] == []
    assert len(client.calls) == 1


def test_greeting_plus_factual_question_remains_one_grounded_turn(tmp_path: Path) -> None:
    engine, client = make_engine(
        envelope(
            "grounded_answer",
            "Цена указана в прайс-листе.",
            [PRICE_REF],
            [{"source_ref": PRICE_REF, "quote": "Цена прыжка на 10 прыжков — 12 000 ₽."}],
        ),
        profile_root=make_profile_root(tmp_path),
    )

    result = engine.answer(question="Здравствуйте, сколько стоит прыжок?", history=[])

    assert result["kind"] == "grounded_answer"
    assert len(client.calls) == 1


def test_poisoned_internal_context_is_not_sent_to_model(tmp_path: Path) -> None:
    engine, client = make_engine(envelope("grounded_answer", "Сертификат доступен.", [CERTIFICATE_REF]), profile_root=make_profile_root(tmp_path))

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


def test_corpus_over_budget_fails_closed_without_call(tmp_path: Path) -> None:
    engine, client = make_engine(envelope("social_reply", "Здравствуйте"), profile_root=make_profile_root(tmp_path), max_corpus_chars=1)

    with pytest.raises(CorpusTooLargeError):
        engine.answer(question="Здравствуйте", history=[])

    assert client.calls == []


def test_provider_exhaustion_keeps_safe_provider_failure_telemetry(tmp_path: Path) -> None:
    engine, client = make_engine(LLMRecoveryExhausted("all slots exhausted"), profile_root=make_profile_root(tmp_path))
    client.last_call_info.update({"error": "HTTP 429", "attempts": 4})

    result = engine.answer(question="Сколько стоит прыжок?", history=[])

    assert result["kind"] == "retry_pending"
    assert result["telemetry"]["provider_error"] == "HTTP 429"
    assert result["telemetry"]["provider_attempt_count"] == 4


def test_provider_exhaustion_returns_retry_pending_without_customer_text(tmp_path: Path) -> None:
    engine, client = make_engine(LLMRecoveryExhausted("all slots exhausted"), profile_root=make_profile_root(tmp_path))

    result = engine.answer(question="Сколько стоит прыжок?", history=[])

    assert result["kind"] == "retry_pending"
    assert result["response_text"] == ""
    assert result["source_refs"] == []
    assert len(client.calls) == 1


def test_input_projection_loads_runtime_artifact_and_bounds_role_labelled_history(tmp_path: Path) -> None:
    engine, _ = make_engine(envelope("social_reply", "Здравствуйте"), profile_root=make_profile_root(tmp_path))
    history = [
        {"role": "user", "content": f"message-{index}"}
        for index in range(12)
    ] + [{"role": "assistant", "content": "previous answer"}, {"role": "system", "content": "ignored"}]

    packet = engine.build_input(question="new question", history=history)

    assert packet["question"] == "new question"
    assert packet["history"] == history[-11:-1]
    assert [fact["id"] for fact in packet["corpus"]["facts"]] == [PRICE_REF, CERTIFICATE_REF]


def test_date_observations_are_projected_before_the_one_model_call(tmp_path: Path) -> None:
    from app.services.tool_runtime import ToolRuntimeService

    tool_result = ToolRuntimeService().collect(text="Можно 25 июня?", kb_hits=[])
    engine, client = make_engine(envelope("grounded_answer", "Уточните доступность даты.", [PRICE_REF]), profile_root=make_profile_root(tmp_path))

    engine.answer(question="Можно 25 июня?", history=[], tool_observations=tool_result["tool_results"])

    payload = json.loads(str(client.calls[0]["user_prompt"]))
    assert payload["tool_observations"][0]["kind"] == "calendar_weekday"
    assert ToolRuntimeService().collect(text="Какие есть сертификаты?", kb_hits=[])["tool_results"] == []


def test_month_range_period_observations_use_public_projection_without_exact_dates_or_weekend_dates(tmp_path: Path) -> None:
    from app.services.tool_runtime import ToolRuntimeService

    runtime = ToolRuntimeService()
    collected = runtime.collect(text="Можно прыгнуть в августе?", kb_hits=[])
    engine, client = make_engine(envelope("grounded_answer", "Можно ориентироваться на сезонный график.", [PRICE_REF]), profile_root=make_profile_root(tmp_path))

    assert collected["tool_results"][0]["kind"] == "calendar_period_weekends"
    assert collected["tool_results"][0]["summary"] == "В период в августе календарные выходные: 01.08.2026, 02.08.2026, 08.08.2026, 09.08.2026, 15.08.2026, 16.08.2026, 22.08.2026, 23.08.2026, 29.08.2026, 30.08.2026. Эти даты являются только календарными ориентирами."
    assert collected["tool_trace"][0]["result"]["weekend_dates"] == [
        "2026-08-01",
        "2026-08-02",
        "2026-08-08",
        "2026-08-09",
        "2026-08-15",
        "2026-08-16",
        "2026-08-22",
        "2026-08-23",
        "2026-08-29",
        "2026-08-30",
    ]

    public = runtime.project_public_period_observation(collected["tool_results"][0])
    assert public["kind"] == "calendar_period_public"
    assert public["summary"] == "В период в августе"
    assert "weekend_dates" not in public["structured"]

    engine.answer(question="Можно прыгнуть в августе?", history=[], tool_observations=collected["tool_results"])

    payload = json.loads(str(client.calls[0]["user_prompt"]))
    obs = payload["tool_observations"][0]
    assert obs["kind"] == "calendar_period_public"
    assert obs["summary"] == "В период в августе"
    assert obs["structured"]["original_period"] == "в августе"
    assert obs["structured"]["start_month"] == 8
    assert obs["structured"]["end_month"] == 8
    assert "weekend_dates" not in obs["structured"]
    assert "13.08" not in json.dumps(payload, ensure_ascii=False)
    assert "2026-08" not in json.dumps(payload, ensure_ascii=False)


def test_august_grounded_general_answer_succeeds_without_forbidden_exact_dates_for_period(tmp_path: Path) -> None:
    from app.services.tool_runtime import ToolRuntimeService

    observations = ToolRuntimeService().collect(text="Можно прыгнуть в августе?", kb_hits=[])["tool_results"]
    engine, client = make_engine(
        envelope("grounded_answer", "В августе обычно ориентируются на сезонный график.", [PRICE_REF], [{"source_ref": PRICE_REF, "quote": PRICE_PAGE}]),
        profile_root=make_profile_root(tmp_path),
    )

    result = engine.answer(question="Можно прыгнуть в августе?", history=[], tool_observations=observations)

    assert result["kind"] == "grounded_answer"
    assert result["telemetry"]["contract_error"] is None
    assert len(client.calls) == 1


def test_public_period_observation_still_blocks_exact_dates_in_validated_customer_text(tmp_path: Path) -> None:
    from app.services.tool_runtime import ToolRuntimeService

    exact_date_quote = "Дата календарного ориентира — 01.08.2026."
    profile_root = make_profile_root(tmp_path)
    (profile_root / "kb" / PRICE_PAGE_PATH).write_text(
        f"{PRICE_PAGE}\n{exact_date_quote}",
        encoding="utf-8",
    )
    write_runtime_artifact(profile_root, price_text=f"{PRICE_PAGE}\n{exact_date_quote}")
    observations = ToolRuntimeService().collect(text="Можно прыгнуть в августе?", kb_hits=[])["tool_results"]
    engine, client = make_engine(
        envelope(
            "grounded_answer",
            exact_date_quote,
            [PRICE_REF],
            [{"source_ref": PRICE_REF, "quote": exact_date_quote}],
        ),
        profile_root=profile_root,
    )

    result = engine.answer(
        question="Можно прыгнуть в августе?",
        history=[],
        tool_observations=observations,
    )

    assert result["kind"] == "cannot_answer"
    assert result["response_text"] == ""
    assert result["telemetry"]["contract_error"] == "forbidden_exact_dates_for_period"
    assert len(client.calls) == 1


@pytest.mark.parametrize("structured", [None, "broken", ["2026-08-01"]])
def test_malformed_period_observation_is_still_projected_without_exact_date_leak(
    tmp_path: Path,
    structured: object,
) -> None:
    engine, client = make_engine(
        envelope("cannot_answer", "Нет подтверждённой информации."),
        profile_root=make_profile_root(tmp_path),
    )

    engine.answer(
        question="Можно прыгнуть в августе?",
        history=[],
        tool_observations=[
            {
                "kind": "calendar_period_weekends",
                "summary": "Выходные даты: 01.08.2026, 02.08.2026.",
                "structured": structured,
            }
        ],
    )

    payload = json.loads(str(client.calls[0]["user_prompt"]))
    observation = payload["tool_observations"][0]
    assert observation["kind"] == "calendar_period_public"
    assert observation["structured"] == {
        "original_period": None,
        "start_month": None,
        "end_month": None,
        "year": None,
    }
    assert "01.08.2026" not in json.dumps(observation, ensure_ascii=False)
    assert "02.08.2026" not in json.dumps(observation, ensure_ascii=False)
    assert len(client.calls) == 1


def test_concrete_single_date_weekday_observation_remains_unchanged_supported(tmp_path: Path) -> None:
    from app.services.tool_runtime import ToolRuntimeService

    observations = ToolRuntimeService().collect(text="Можно ли прыгнуть 25 июня?", kb_hits=[])["tool_results"]
    engine, client = make_engine(envelope("grounded_answer", "Дата будний день.", [PRICE_REF], [{"source_ref": PRICE_REF, "quote": PRICE_PAGE}]), profile_root=make_profile_root(tmp_path))

    engine.answer(question="Можно ли прыгнуть 25 июня?", history=[], tool_observations=observations)

    payload = json.loads(str(client.calls[0]["user_prompt"]))
    assert payload["tool_observations"][0]["kind"] == "calendar_weekday"


@pytest.mark.parametrize(
    ("question", "expected_kind"),
    [("Можно прыгнуть завтра?", "calendar_weekday"), ("Можно прыгнуть в августе?", "calendar_period_public")],
)
def test_relative_date_and_period_observations_are_projected_before_one_call(tmp_path: Path, question: str, expected_kind: str) -> None:
    from app.services.tool_runtime import ToolRuntimeService

    observations = ToolRuntimeService().collect(text=question, kb_hits=[])["tool_results"]
    engine, client = make_engine(envelope("cannot_answer", "Нет подтверждённой информации."), profile_root=make_profile_root(tmp_path))

    engine.answer(question=question, history=[], tool_observations=observations)

    payload = json.loads(str(client.calls[0]["user_prompt"]))
    assert payload["tool_observations"][0]["kind"] == expected_kind
    assert len(client.calls) == 1


def test_invalid_runtime_knowledge_artifact_fails_closed_without_call(tmp_path: Path) -> None:
    profile_root = make_profile_root(tmp_path)
    (profile_root / "kb" / "knowledge-base.v1.json").write_text('{"version": 1, "facts": []}', encoding="utf-8")
    client = CapturingClient(envelope("grounded_answer", "Не должен быть вызван.", [PRICE_REF]))
    engine = SimpleAnswerEngine(client=client, profile_root=profile_root)

    with pytest.raises(ValueError, match="runtime knowledge artifact"):
        engine.answer(question="Сколько стоит?", history=[])

    assert client.calls == []


def test_followup_projects_only_ten_role_labelled_prior_messages_without_summary(tmp_path: Path) -> None:
    engine, client = make_engine(envelope("cannot_answer", "Нет подтверждённого ответа."), profile_root=make_profile_root(tmp_path))
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


def test_period_answer_with_forbidden_exact_dates_fails_closed(tmp_path: Path) -> None:
    engine, client = make_engine(
        envelope(
            "grounded_answer",
            "В августе можно 01.08.2026 и 02.08.2026.",
            [PRICE_REF],
            [{"source_ref": PRICE_REF, "quote": "Цена прыжка на 10 прыжков — 12 000 ₽."}],
        ),
        profile_root=make_profile_root(tmp_path),
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

    assert result["kind"] == "grounded_answer"
    assert result["response_text"] == "Цена прыжка на 10 прыжков — 12 000 ₽."
    assert result["telemetry"]["contract_error"] is None
    assert len(client.calls) == 1


def test_simple_engine_projects_period_public_summary_without_leaking_raw_dates(tmp_path: Path) -> None:
    from app.services.tool_runtime import ToolRuntimeService

    runtime = ToolRuntimeService()
    collected = runtime.collect(text="Можно прыгнуть в августе?", kb_hits=[])

    assert collected["tool_results"][0]["summary"] == "В период в августе календарные выходные: 01.08.2026, 02.08.2026, 08.08.2026, 09.08.2026, 15.08.2026, 16.08.2026, 22.08.2026, 23.08.2026, 29.08.2026, 30.08.2026. Эти даты являются только календарными ориентирами."
    projected = runtime.project_public_period_observation(collected["tool_results"][0])
    assert projected["summary"] == "В период в августе"
    assert "2026-08-01" not in json.dumps(projected, ensure_ascii=False)

    engine, client = make_engine(envelope("grounded_answer", "Можно ориентироваться на сезонный график.", [PRICE_REF]), profile_root=make_profile_root(tmp_path))
    engine.answer(question="Можно прыгнуть в августе?", history=[], tool_observations=collected["tool_results"])

    payload = json.loads(str(client.calls[0]["user_prompt"]))
    assert payload["tool_observations"][0]["summary"] == "В период в августе"
    assert "2026-08-01" not in json.dumps(payload, ensure_ascii=False)
