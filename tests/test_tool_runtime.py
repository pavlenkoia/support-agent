from __future__ import annotations

from datetime import UTC, datetime

from app.services import direct_llm as direct_llm_module
from app.services.direct_llm import DirectLLMService
from app.services import tool_runtime as tool_runtime_module
from app.services.tool_runtime import ToolRuntimeService


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 7, 13, 7, 9, 21, tzinfo=tz or UTC)


def test_tool_runtime_resolves_relative_dates_from_runtime_context(monkeypatch) -> None:
    monkeypatch.setattr(tool_runtime_module, "datetime", FrozenDateTime)
    service = ToolRuntimeService()

    cases = [
        ("Можно сегодня заехать в офис к 15:00 за сертификатом?", "2026-07-13"),
        ("Офис сегодня работает?", "2026-07-13"),
        ("Можно ли прыгнуть сегодня?", "2026-07-13"),
        ("Можно ли завтра?", "2026-07-14"),
        ("Можно ли послезавтра?", "2026-07-15"),
    ]

    for text, expected_iso_date in cases:
        result = service.collect(text=text, kb_hits=[])
        assert result["tool_status"] == "used"
        assert result["tool_trace"][0]["tool_name"] == "calendar_weekday"
        assert result["tool_trace"][0]["input"]["iso_date"] == expected_iso_date


def test_tool_runtime_does_not_treat_office_hours_as_weekend_jump_check(monkeypatch) -> None:
    monkeypatch.setattr(tool_runtime_module, "datetime", FrozenDateTime)
    service = ToolRuntimeService()

    result = service.collect(
        text="Можно сегодня заехать в офис к 15 часам для приобретения подарочного сертификата?",
        kb_hits=[
            {"text": "Подарочные сертификаты можно купить онлайн и в офисе по будням."},
            {"text": "Прыжки обычно проходят по выходным."},
        ],
    )

    assert result["tool_trace"][0]["input"]["iso_date"] == "2026-07-13"
    assert [item["kind"] for item in result["tool_results"]] == ["calendar_weekday"]


def test_direct_llm_grounded_fallback_ignores_weekend_kb_for_office_question() -> None:
    service = DirectLLMService(client=None)

    result = service._fallback_answer_from_grounding(
        text="Можно сегодня заехать в офис к 15 часам для приобретения подарочного сертификата?",
        answer_context=[
            {"text": "Подарочный сертификат можно купить онлайн на сайте или в офисе по будням с 09:00 до 17:00."},
            {"text": "Прыжки обычно проходят по выходным."},
        ],
        kb_hits=[],
        conversation_context={
            "tool_observations": [
                {
                    "kind": "calendar_weekday",
                    "summary": "Дата 2026-07-13 приходится на понедельник.",
                    "structured": {"weekday_ru": "понедельник", "is_weekend": False},
                }
            ]
        },
        reason="test_fallback",
    )

    assert result is not None
    assert "Прыжки обычно проходят по выходным" not in result["response_text"]
    assert "офисе по будням" in result["response_text"]


def test_direct_llm_runtime_error_returns_grounded_kb_answer(monkeypatch) -> None:
    class BrokenClient:
        def generate(self, **kwargs):
            raise RuntimeError("IncompleteRead")

    monkeypatch.setattr(direct_llm_module.settings, "direct_llm_provider", "mistral")
    result = DirectLLMService(client=BrokenClient()).respond(
        "Можно ли записаться на самостоятельный прыжок?",
        {
            "kb_status": "found",
            "grounding_status": "ready",
            "answer_context": [
                {
                    "text": (
                        "На самостоятельный прыжок можно записаться по телефону +7 (351) 214-30-30, "
                        "добавочный 1. Прыжки обычно проходят по выходным; точную дату уточняйте по телефону."
                    ),
                    "source_ref": "contacts.md",
                }
            ],
        },
    )

    assert result["route"] == "answer"
    assert "+7 (351) 214-30-30" in result["response_text"]
    assert result["reason"] == "prompt_runtime_grounded_fallback:RuntimeError"


def test_direct_llm_ready_grounding_never_degrades_to_cannot_answer_when_secondary_fallback_fails(monkeypatch) -> None:
    class BrokenClient:
        def generate(self, **kwargs):
            raise RuntimeError("IncompleteRead")

    monkeypatch.setattr(direct_llm_module.settings, "direct_llm_provider", "mistral")
    service = DirectLLMService(client=BrokenClient())
    monkeypatch.setattr(service, "_fallback_answer_from_grounding", lambda *args, **kwargs: None)

    result = service.respond(
        "Можно ли в тандеме при весе 120 кг?",
        {
            "kb_status": "found",
            "grounding_status": "ready",
            "grounded_facts": [
                "Максимальный вес для тандем-прыжка — до 85 кг.",
                "При превышении веса необходимо уточнить возможность участия в офисе.",
            ],
            "answer_basis": "При весе 120 кг тандем-прыжок невозможен.",
        },
    )

    assert result["route"] == "answer"
    assert "до 85 кг" in result["response_text"]
    assert result["reason"] == "prompt_runtime_grounded_fallback:RuntimeError"
    assert any(item.get("step") == "grounded_fallback" for item in result["llm_trace"])


def test_direct_llm_runtime_error_uses_only_kb_grounded_facts(monkeypatch) -> None:
    class BrokenClient:
        def generate(self, **kwargs):
            raise RuntimeError("IncompleteRead")

    monkeypatch.setattr(direct_llm_module.settings, "direct_llm_provider", "mistral")
    result = DirectLLMService(client=BrokenClient()).respond(
        "Добрый день, можно ли записаться на самостоятельный прыжок",
        {
            "kb_status": "found",
            "grounding_status": "ready",
            "answer_context": [
                {
                    "text": (
                        "Самостоятельный прыжок требует подготовки 3–4 часа в день прыжка. "
                        "Если клиент спрашивает, можно ли прыгнуть завтра, нужно уточнить анонс. "
                        "Прыжки обычно проходят по выходным."
                    ),
                    "source_ref": "skydiving-services.md",
                }
            ],
            "grounded_facts": [
                "На самостоятельный прыжок можно записаться по телефону +7 (351) 214-30-30, добавочный 1.",
                "Обычно запись проходит в пятницу после 12:00 на субботу и в субботу после 12:00 на воскресенье.",
            ],
            "answer_basis": "Запись на самостоятельный прыжок: телефон и время записи.",
        },
    )

    assert result["route"] == "answer"
    assert "+7 (351) 214-30-30" in result["response_text"]
    assert "пятницу после 12:00" in result["response_text"]
    assert "3–4 часа" not in result["response_text"]
    assert "прыгнуть завтра" not in result["response_text"]


def test_tool_runtime_resolves_month_range_to_weekend_dates(monkeypatch) -> None:
    monkeypatch.setattr(tool_runtime_module, "datetime", FrozenDateTime)
    service = ToolRuntimeService()

    result = service.collect(
        text="Какие выходные с мая по июнь бывают для прыжков?",
        kb_hits=[{"text": "Прыжки обычно проходят по выходным."}],
    )

    assert result["tool_status"] == "used"
    period = next(item for item in result["tool_results"] if item["kind"] == "calendar_period_weekends")
    assert period["structured"]["original_period"] == "с мая по июнь"
    assert period["structured"]["start_month"] == 5
    assert period["structured"]["end_month"] == 6
    assert period["structured"]["weekend_dates"][0] == "2026-05-02"
    assert period["structured"]["weekend_dates"][-1] == "2026-06-28"


def test_tool_runtime_resolves_month_list_and_season(monkeypatch) -> None:
    monkeypatch.setattr(tool_runtime_module, "datetime", FrozenDateTime)
    service = ToolRuntimeService()

    month_list = service.collect(text="В мае, июне и июле какие выходные?", kb_hits=[])
    season = service.collect(text="Какие выходные летом?", kb_hits=[])

    month_list_period = next(item for item in month_list["tool_results"] if item["kind"] == "calendar_period_weekends")
    season_period = next(item for item in season["tool_results"] if item["kind"] == "calendar_period_weekends")
    assert month_list_period["structured"]["start_month"] == 5
    assert month_list_period["structured"]["end_month"] == 7
    assert season_period["structured"]["start_month"] == 6
    assert season_period["structured"]["end_month"] == 8


def test_tool_runtime_normalizes_month_list_without_trailing_request_clause(monkeypatch) -> None:
    monkeypatch.setattr(tool_runtime_module, "datetime", FrozenDateTime)
    result = ToolRuntimeService().collect(
        text="Будут прыжки в тандеме в августе, сентябре и если известны даты, чтобы запланировать",
        kb_hits=[],
    )

    period = next(item for item in result["tool_results"] if item["kind"] == "calendar_period_weekends")
    assert period["structured"]["original_period"] == "в августе и сентябре"


def test_direct_llm_period_fallback_preserves_requested_period_and_adds_conditions() -> None:
    service = DirectLLMService(client=None)

    result = service._fallback_answer_from_grounding(
        text="А с мая по сентябрь?",
        answer_context=[{"text": "Прыжки обычно проходят по выходным."}],
        kb_hits=[],
        conversation_context={
            "recent_messages": [
                {"role": "user", "content": "Когда обычно проходят прыжки?"},
                {"role": "assistant", "content": "Обычно по выходным."},
            ],
            "tool_observations": [
                {
                    "kind": "calendar_period_weekends",
                    "structured": {
                        "original_period": "с мая по сентябрь",
                        "weekend_dates": ["2026-05-02", "2026-05-03"],
                    },
                }
            ]
        },
        reason="planner_requested_but_no_tool_match",
    )

    assert result is not None
    assert "с мая по сентябрь" in result["response_text"]
    assert "после этих месяцев" not in result["response_text"].lower()
    assert "анонс" in result["response_text"].lower()
    assert "погод" in result["response_text"].lower()


def test_direct_llm_period_fallback_does_not_duplicate_preposition() -> None:
    service = DirectLLMService(client=None)
    result = service._fallback_answer_from_grounding(
        text="Будут прыжки в тандеме в августе и сентябре?",
        answer_context=[{"text": "Прыжки обычно проходят по выходным."}],
        kb_hits=[],
        conversation_context={
            "tool_observations": [
                {"kind": "calendar_period_weekends", "structured": {"original_period": "в августе и сентябре"}},
            ]
        },
        reason="calendar_period_tool_guard",
    )

    assert result is not None
    assert result["response_text"].startswith("В августе и сентябре")
    assert "В период в августе" not in result["response_text"]


def test_direct_llm_period_guard_keeps_calendar_dates_out_of_customer_reply(monkeypatch) -> None:
    class IncorrectCalendarClient:
        def generate(self, **kwargs):
            raise AssertionError("calendar-period response must not delegate dates to the model")

    from app.services import direct_llm as direct_llm_module

    monkeypatch.setattr(direct_llm_module.settings, "direct_llm_provider", "mistral")
    service = DirectLLMService(client=IncorrectCalendarClient())
    result = service.respond(
        "Когда обычно проходят прыжки с мая по июню?",
        {"answer_context": [{"text": "Прыжки обычно проходят по выходным."}]},
        conversation_context={
            "tool_observations": [
                {
                    "kind": "calendar_period_weekends",
                    "structured": {
                        "original_period": "с мая по июню",
                        "weekend_dates": ["2026-05-02", "2026-05-03"],
                    },
                }
            ]
        },
        tool_observations=[
            {
                "kind": "calendar_period_weekends",
                "structured": {
                    "original_period": "с мая по июню",
                    "weekend_dates": ["2026-05-02", "2026-05-03"],
                },
            }
        ],
    )

    assert "02.05.2026" not in result["response_text"]
    assert "03.05.2026" not in result["response_text"]
    assert "анонс" in result["response_text"].lower()
    assert "погод" in result["response_text"].lower()
