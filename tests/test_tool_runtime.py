from __future__ import annotations

from datetime import UTC, datetime

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
