import json
from pathlib import Path

from app.services.jev_answer_engine import JevAnswerEngine, TURN_TYPES


FACTS = [
    {"id": "jump.announcement", "text": "Подтверждение даты прыжков публикуется в анонсе группы за несколько дней до выходных.", "conditions": [], "source_refs": ["schedule:01"]},
    {"id": "certificate.validity", "text": "Сертификат действует один год с даты покупки.", "conditions": [], "source_refs": ["certificate:01"]},
]


class ChoiceClient:
    def __init__(self, choices):
        self.choices = iter(choices)
        self.calls = []

    def choose(self, *, state, question, criteria, instructions):
        self.calls.append({"state": state, "question": question, "criteria": criteria, "instructions": instructions})
        return next(self.choices)


class Writer:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.outputs)


def profile(tmp_path: Path):
    root = tmp_path / "profile"
    (root / "kb").mkdir(parents=True)
    (root / "kb" / "knowledge-base.v1.json").write_text(json.dumps({"schema_version": 1, "facts": FACTS}, ensure_ascii=False))
    (root / "profile.yaml").write_text('role: "Консультант"\nscope:\n  in_scope: ["прыжки"]\n  out_of_scope: ["общие темы"]\nfallback_policy:\n  no_answer:\n    evidence:\n      source_ref: "office:01"\n      text: "Для уточнения вопроса можно позвонить в офис в рабочее время."\n')
    (root / "JEV_ANSWER_PROMPT.md").write_text("Ответь естественно только по выбранному факту.")
    (root / "JEV_DIALOGUE_PROMPT.md").write_text("Отвечай кратко в роли консультанта по предмету поддержки без новых бизнес-фактов.")
    return root


def engine(tmp_path, choices, outputs=()):
    selector = ChoiceClient(choices)
    writer = Writer(outputs)
    return JevAnswerEngine(selector=selector, writer=writer, profile_root=profile(tmp_path)), selector, writer


def test_selected_fact_is_only_business_evidence_and_composite_chooses_main(tmp_path):
    llm, selector, writer = engine(tmp_path, ["substantive", "jump.announcement"], [json.dumps({"response_text": "Дата будет подтверждена в анонсе группы за несколько дней до выходных."}, ensure_ascii=False)])
    result = llm.answer(question="Здравствуйте, будут прыжки 26-го и сколько сертификат действует?", history=[{"role": "user", "content": "Планирую поездку"}],
                        tool_observations=[{"kind": "calendar_weekday", "summary": "Дата 2026-10-26 приходится на понедельник.", "structured": {"year": 2026}}])
    assert result["kind"] == "grounded_answer"
    assert result["source_refs"] == ["jump.announcement"]
    assert result["telemetry"]["selected_fact_id"] == "jump.announcement"
    assert result["telemetry"]["selected_fact"] == FACTS[0]
    assert len(result["telemetry"]["kb_sha256"]) == 64
    assert len(selector.calls) == 2
    assert set(selector.calls[1]["criteria"]) == {"jump.announcement", "certificate.validity", "no_answer"}
    assert "подтверждённый способ дальнейшего действия" in selector.calls[1]["instructions"]
    packet = json.loads(writer.calls[0]["user_prompt"])
    assert packet["selected_facts"] == [{k: FACTS[0][k] for k in ("id", "text", "conditions")}]
    assert packet["question"] == "Здравствуйте, будут прыжки 26-го и сколько сертификат действует?"
    assert packet["history"] == [{"role": "user", "content": "Планирую поездку"}]
    assert "certificate.validity" not in writer.calls[0]["user_prompt"]
    assert "calendar_weekday" not in writer.calls[0]["user_prompt"]
    assert writer.calls[0]["system_prompt"] == "Ответь естественно только по выбранному факту."


def test_no_answer_uses_configured_fallback_without_writer(tmp_path):
    llm, selector, writer = engine(tmp_path, ["substantive", "no_answer"])
    result = llm.answer(question="Есть ли неизвестная услуга?", history=[])
    assert result["kind"] == "cannot_answer"
    assert result["response_text"] == "Для уточнения вопроса можно позвонить в офис в рабочее время."
    assert writer.calls == []


def test_live_profile_no_answer_literal_is_approved_text():
    from app.services.policy import PolicyService

    policy = PolicyService(str(Path(__file__).resolve().parents[1] / "deploy" / "profile"))
    assert policy.no_answer_policy_evidence()[0]["text"] == (
        "К сожалению, я не могу ответить на ваш вопрос. Я уже подключил к решению специалиста. "
        "Он подготовит ответ и напишет вам в этом чате."
    )


def test_expressed_interest_uses_intent_and_attachment_context_for_fact_choice(tmp_path):
    llm, selector, writer = engine(tmp_path, ["substantive", "jump.announcement"],
                                  [json.dumps({"response_text": "Условия сообщают в анонсе группы."}, ensure_ascii=False)])
    question = "Здравствуйте! Меня заинтересовала эта услуга.\nКонтекст VK Market-вложения:\nНазвание: Прыжок"
    result = llm.answer(question=question, history=[])

    assert result["kind"] == "grounded_answer"
    assert result["telemetry"]["selected_fact_id"] == "jump.announcement"
    assert "выраженное намерение" in TURN_TYPES["substantive"]
    assert "вопросительная форма не обязательны" in TURN_TYPES["substantive"]
    assert "информационную потребность или намерение" in selector.calls[1]["instructions"]
    assert "контекста вложения" in selector.calls[1]["instructions"]
    assert "выраженный интерес к конкретной услуге" in selector.calls[1]["instructions"]
    assert "нет подтверждённого факта о подходящем следующем шаге" in selector.calls[1]["criteria"]["no_answer"]
    assert selector.calls[1]["state"]["message"] == question
    assert json.loads(writer.calls[0]["user_prompt"])["selected_facts"][0]["id"] == "jump.announcement"


def test_choice_client_rejects_unlisted_and_zero_probability_choices():
    from unittest.mock import patch
    from io import BytesIO
    import pytest
    from app.services.jev_answer_engine import JevChoiceClient

    client = JevChoiceClient("test-secret")
    criteria = {"fact.one": "One", "no_answer": "No fact"}
    for choice, probabilities in (("not-listed", {"fact.one": 1, "no_answer": 0}),
                                  ("fact.one", {"fact.one": 0, "no_answer": 1})):
        raw = json.dumps({"answers": {"fact": {"choice": choice, "probabilities": probabilities}}}).encode()
        with patch("app.services.jev_answer_engine.urlopen", return_value=BytesIO(raw)):
            with pytest.raises(ValueError, match="invalid Jev choice"):
                client.choose(state={"message": "test"}, question="fact", criteria=criteria, instructions="Select")


def test_social_acknowledgement_is_not_flood_or_fact_selection(tmp_path):
    llm, selector, writer = engine(tmp_path, ["social"], [json.dumps({"response_text": "Пожалуйста!"}, ensure_ascii=False)])
    result = llm.answer(question="Понял, спасибо", history=[])
    assert result["kind"] == "social_reply"
    assert result["response_text"] == "Пожалуйста!"
    assert len(selector.calls) == 1
    assert "knowledge_base" not in writer.calls[0]["user_prompt"]


def test_social_writer_uses_latest_turn_without_history_and_accepts_plain_content(tmp_path):
    llm, selector, writer = engine(tmp_path, ["social"], ["Пожалуйста!"])
    result = llm.answer(question="Понял, спасибо", history=[{"role": "user", "content": "Вопрос о сертификате"}])
    assert result["kind"] == "social_reply"
    assert result["response_text"] == "Пожалуйста!"
    assert selector.calls[0]["state"]["history"] == [{"role": "user", "content": "Вопрос о сертификате"}]
    assert "history" not in json.loads(writer.calls[0]["user_prompt"])
    assert "think" not in writer.calls[0]


def test_social_writer_rejects_empty_or_structured_invalid_content(tmp_path):
    for raw in ("", '{"response_text": invalid}', "размышляю\nа потом отвечу"):
        llm, _, _ = engine(tmp_path / str(len(raw)), ["social"], [raw])
        result = llm.answer(question="Спасибо", history=[])
        assert result["kind"] == "retry_pending"
        assert result["response_text"] == ""


def test_offtopic_and_flood_stay_distinct_from_no_answer(tmp_path):
    for kind in ("off_topic", "flood"):
        llm, selector, writer = engine(tmp_path / kind, [kind], [json.dumps({"response_text": "Я помогу с вопросами по услугам нашего клуба."}, ensure_ascii=False)])
        result = llm.answer(question="Посторонний текст", history=[])
        assert result["kind"] == "out_of_scope"
        assert result["telemetry"]["turn_type"] == kind
        assert writer.calls
        assert len(selector.calls) == 1


def test_plain_and_json_content_from_same_selected_fact_are_delivered(tmp_path):
    answer = "Дата прыжков подтверждается в анонсе группы за несколько дней до выходных."
    for index, output in enumerate((answer, json.dumps({"response_text": answer}, ensure_ascii=False))):
        llm, _, _ = engine(tmp_path / str(index), ["substantive", "jump.announcement"], [output])
        result = llm.answer(question="Когда подтвердится дата прыжков?", history=[])
        assert result["kind"] == "grounded_answer"
        assert result["response_text"] == answer
        assert result["source_refs"] == ["jump.announcement"]


def test_invalid_structured_content_or_conflicting_fact_id_is_not_delivered(tmp_path):
    for output in ('{"response_text": invalid}', json.dumps({"response_text": "Звоните по выдуманному номеру.", "evidence": [{"fact_id": "certificate.validity", "quote": "Другое"}]}, ensure_ascii=False)):
        llm, _, _ = engine(tmp_path / str(len(output)), ["substantive", "jump.announcement"], [output])
        result = llm.answer(question="Будут прыжки 26-го?", history=[])
        assert result["kind"] == "retry_pending"
        assert result["response_text"] == ""
        assert result["telemetry"]["selected_fact"] == FACTS[0]


def test_selector_failure_does_not_use_no_answer_fallback(tmp_path):
    llm, selector, writer = engine(tmp_path, ["bad_type"])
    result = llm.answer(question="Вопрос", history=[])
    assert result["kind"] == "retry_pending"
    assert result["response_text"] == ""
    assert writer.calls == []


def test_profile_build_includes_both_jev_prompts(tmp_path):
    from scripts.build_runtime_profile import build_runtime_profile
    source = tmp_path / "source"
    source.mkdir()
    (source / "kb" / "concepts").mkdir(parents=True)
    (source / "kb" / "concepts" / "fact.md").write_text(
        '---\nruntime_facts:\n  - id: fact.one\n    text: Test fact.\n    conditions: []\n    source_refs: ["test:01"]\n---\n')
    (source / "SIMPLE_ANSWER_PROMPT.md").write_text("Current contract")
    (source / "JEV_ANSWER_PROMPT.md").write_text("Selected fact contract")
    (source / "JEV_DIALOGUE_PROMPT.md").write_text("Social contract")
    target = tmp_path / "built"
    build_runtime_profile(source, target)
    assert (target / "JEV_ANSWER_PROMPT.md").read_text() == "Selected fact contract"
    assert (target / "JEV_DIALOGUE_PROMPT.md").read_text() == "Social contract"


def test_selected_fact_prompt_forbids_emotional_self_expression():
    source = Path(__file__).resolve().parents[1] / "deploy" / "profile-source" / "JEV_ANSWER_PROMPT.md"
    prompt = source.read_text(encoding="utf-8")
    assert "Не выражай эмоций, личного отношения" in prompt
    assert "не как источник фактов или готовых формулировок" in prompt
    assert "не обещай помочь" in prompt
    assert "Не задавай клиенту встречных вопросов" in prompt


def test_social_prompt_is_short_and_scope_based():
    source = Path(__file__).resolve().parents[1] / "deploy" / "profile-source" / "JEV_DIALOGUE_PROMPT.md"
    prompt = source.read_text(encoding="utf-8")
    assert "Область поддержки указана в переданном scope" in prompt
    assert "одной короткой фразой" in prompt
    assert "Не повторяй слова клиента" in prompt
    assert "приглашение обратиться снова" in prompt
    assert "парашютных прыжков" not in prompt


def test_prompts_do_not_ask_customers_followup_questions():
    root = Path(__file__).resolve().parents[1] / "deploy" / "profile-source"
    answer = (root / "JEV_ANSWER_PROMPT.md").read_text(encoding="utf-8")
    dialogue = (root / "JEV_DIALOGUE_PROMPT.md").read_text(encoding="utf-8")
    assert "задай только один прямой нейтральный вопрос" not in answer
    assert "попроси его уточнить" not in dialogue


def test_dialogue_policies_are_not_selectable_kb_facts(tmp_path):
    from scripts.build_runtime_profile import build_runtime_profile
    source = Path(__file__).resolve().parents[1] / "deploy" / "profile-source"
    compiled = build_runtime_profile(source, tmp_path / "compiled")
    facts = json.loads((tmp_path / "compiled" / "kb" / "knowledge-base.v1.json").read_text(encoding="utf-8"))["facts"]
    ids = {fact["id"] for fact in facts}
    assert "communication.generic-interest" not in ids
    assert "communication.unclear-code" not in ids
