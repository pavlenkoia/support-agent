from pathlib import Path


def test_selected_fact_prompt_separates_internal_sources_from_customer_answer():
    prompt_path = (
        Path(__file__).resolve().parents[1]
        / "deploy/profile-source/JEV_ANSWER_PROMPT.md"
    )
    prompt = prompt_path.read_text(encoding="utf-8")
    assert (
        "Не описывай внутренние источники, отбор фактов, устройство системы "
        "или обработку сведений."
    ) in prompt
    assert (
        "Отсутствие сведений не подтверждает отсутствие условия или ограничения."
    ) in prompt
    assert (
        "Если не можешь ответить на часть запроса, сообщи об этом кратко, "
        "без описания внутренних причин и без предположений."
    ) in prompt
    assert "не как источник фактов или готовых формулировок" in prompt
    assert "Не задавай клиенту встречных вопросов" in prompt
