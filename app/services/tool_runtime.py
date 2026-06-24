from __future__ import annotations

import re
from datetime import UTC, datetime, date
from typing import Any


MONTHS_RU = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}
WEEKDAY_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
NUMERIC_DATE_RE = re.compile(r"(?<!\d)(?P<day>\d{1,2})[./-](?P<month>\d{1,2})(?:[./-](?P<year>\d{4}))?(?!\d)")
TEXT_DATE_RE = re.compile(
    r"(?<!\d)(?P<day>\d{1,2})\s+(?P<month_name>января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)(?:\s+(?P<year>\d{4}))?(?!\d)",
    flags=re.IGNORECASE,
)
DAY_ONLY_RE = re.compile(r"(?<!\d)(?P<day>\d{1,2})(?:-?го|\s*числа)?(?!\d)", flags=re.IGNORECASE)


class ToolRuntimeService:
    def collect(self, *, text: str, kb_hits: list[dict], conversation_context: dict | None = None) -> dict[str, Any]:
        _ = conversation_context
        observations: list[dict[str, Any]] = []
        trace: list[dict[str, Any]] = []

        parsed_date = self._extract_date(text)
        if parsed_date is None:
            return {
                "tool_status": "skipped",
                "tool_results": [],
                "tool_trace": [],
            }

        weekday_index = parsed_date.weekday()
        weekday_ru = WEEKDAY_RU[weekday_index]
        is_weekend = weekday_index >= 5
        calendar_result = {
            "tool_name": "calendar_weekday",
            "input": {"iso_date": parsed_date.isoformat()},
            "result": {
                "weekday_index": weekday_index,
                "weekday_ru": weekday_ru,
                "is_weekend": is_weekend,
                "year": parsed_date.year,
            },
        }
        trace.append(calendar_result)
        observations.append(
            {
                "kind": "calendar_weekday",
                "summary": f"Дата {parsed_date.isoformat()} приходится на {weekday_ru}.",
                "structured": calendar_result["result"],
            }
        )

        kb_text = "\n".join(str(hit.get("text", "")) for hit in kb_hits).lower()
        if any(token in kb_text for token in ("выходн", "суббот", "воскрес")):
            availability = "может соответствовать правилу про выходные" if is_weekend else "не соответствует правилу про выходные"
            observations.append(
                {
                    "kind": "weekend_rule_check",
                    "summary": (
                        "По календарной проверке дата "
                        f"{parsed_date.isoformat()} — это {weekday_ru}, поэтому она {availability}."
                    ),
                    "structured": {
                        "weekday_ru": weekday_ru,
                        "is_weekend": is_weekend,
                    },
                }
            )

        return {
            "tool_status": "used",
            "tool_results": observations,
            "tool_trace": trace,
        }

    def _extract_date(self, text: str) -> date | None:
        now = datetime.now(UTC).date()
        now_year = now.year

        match = NUMERIC_DATE_RE.search(text)
        if match:
            day = int(match.group("day"))
            month = int(match.group("month"))
            year = int(match.group("year") or now_year)
            return self._safe_date(year, month, day)

        match = TEXT_DATE_RE.search(text)
        if match:
            day = int(match.group("day"))
            month = MONTHS_RU[match.group("month_name").lower()]
            year = int(match.group("year") or now_year)
            return self._safe_date(year, month, day)

        match = DAY_ONLY_RE.search(text)
        if match:
            day = int(match.group("day"))
            return self._nearest_day_only_date(now, day)

        return None

    @staticmethod
    def _nearest_day_only_date(current_date: date, day: int) -> date | None:
        for month_offset in range(0, 13):
            year = current_date.year + ((current_date.month - 1 + month_offset) // 12)
            month = ((current_date.month - 1 + month_offset) % 12) + 1
            candidate = ToolRuntimeService._safe_date(year, month, day)
            if candidate and candidate >= current_date:
                return candidate
        return None

    @staticmethod
    def _safe_date(year: int, month: int, day: int) -> date | None:
        try:
            return date(year, month, day)
        except ValueError:
            return None
