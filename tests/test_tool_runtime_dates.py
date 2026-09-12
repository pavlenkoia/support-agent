from __future__ import annotations

from app.services.tool_runtime import ToolRuntimeService


def test_collect_does_not_treat_quantity_as_calendar_date() -> None:
    result = ToolRuntimeService().collect(
        text="2 прыжка с парашютом, когда прыжки",
        kb_hits=[],
        conversation_context={"calendar_reference_at": "2026-09-12T00:00:00+05:00"},
    )

    assert result["tool_status"] == "skipped"
    assert result["tool_results"] == []


def test_collect_recognizes_explicit_day_of_month() -> None:
    result = ToolRuntimeService().collect(
        text="Можно прыгнуть 2-го числа?",
        kb_hits=[],
        conversation_context={"calendar_reference_at": "2026-09-12T00:00:00+05:00"},
    )

    assert result["tool_status"] == "used"
    assert result["tool_results"][0]["structured"]["year"] == 2026
