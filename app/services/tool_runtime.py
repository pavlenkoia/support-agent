from __future__ import annotations

import re
from datetime import UTC, datetime, date, timedelta
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
MONTH_PERIOD_RE = re.compile(
    r"(?P<prefix>с|по|в)\s+(?P<start>январ(?:ь|я|е)?|феврал(?:ь|я|е)?|март(?:а|е)?|апрел(?:ь|я|е)?|ма(?:й|я|е)|июн(?:ь|я|е)?|июл(?:ь|я|е)?|август(?:а|е)?|сентябр(?:ь|я|е)?|октябр(?:ь|я|е)?|ноябр(?:ь|я|е)?|декабр(?:ь|я|е)?)(?:\s+(?:по|—|-|до)\s+(?P<end>январ(?:ь|я|е)?|феврал(?:ь|я|е)?|март(?:а|е)?|апрел(?:ь|я|е)?|ма(?:й|я|е)|июн(?:ь|я|е)?|июл(?:ь|я|е)?|август(?:а|е)?|сентябр(?:ь|я|е)?|октябр(?:ь|я|е)?|ноябр(?:ь|я|е)?|декабр(?:ь|я|е)?))?",
    flags=re.IGNORECASE,
)
SEASONS_RU = {
    "весна": (3, 4, 5),
    "лето": (6, 7, 8),
    "осень": (9, 10, 11),
    "зима": (12, 1, 2),
}


class ToolRuntimeService:
    def matches_calendar_query(self, text: str) -> bool:
        return self._extract_calendar_period(text) is not None or self._extract_date(text) is not None

    def collect(self, *, text: str, kb_hits: list[dict], conversation_context: dict | None = None) -> dict[str, Any]:
        observations: list[dict[str, Any]] = []
        trace: list[dict[str, Any]] = []

        period = self._extract_calendar_period(text)
        if period is not None:
            weekend_dates = self._weekend_dates_for_period(period["months"], period["year"])
            period_result = {
                "tool_name": "calendar_period_weekends",
                "input": {
                    "original_period": period["original_period"],
                    "months": period["months"],
                    "year": period["year"],
                },
                "result": {
                    "original_period": period["original_period"],
                    "start_month": period["months"][0],
                    "end_month": period["months"][-1],
                    "year": period["year"],
                    "weekend_dates": [item.isoformat() for item in weekend_dates],
                },
            }
            trace.append(period_result)
            observations.append(
                {
                    "kind": "calendar_period_weekends",
                    "summary": (
                        f"В период {period['original_period']} календарные выходные: "
                        + ", ".join(item.strftime("%d.%m.%Y") for item in weekend_dates)
                        + ". Эти даты являются только календарными ориентирами."
                    ),
                    "structured": period_result["result"],
                }
            )
            return {
                "tool_status": "used",
                "tool_results": observations,
                "tool_trace": trace,
            }

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
        if any(token in kb_text for token in ("выходн", "суббот", "воскрес")) and self._is_jump_schedule_request(text, conversation_context):
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

    def _extract_calendar_period(self, text: str) -> dict[str, Any] | None:
        lowered = str(text or "").lower()
        now_year = datetime.now(UTC).year

        for season, months in SEASONS_RU.items():
            season_forms = {
                "весна": ("весной", "весна"),
                "лето": ("летом", "лето"),
                "осень": ("осенью", "осень"),
                "зима": ("зимой", "зима"),
            }[season]
            if any(form in lowered for form in season_forms):
                return {
                    "original_period": self._matched_season_text(lowered, season),
                    "months": list(months),
                    "year": now_year,
                }

        match = MONTH_PERIOD_RE.search(lowered)
        if not match:
            return None
        start_month = self._month_number(match.group("start"))
        end_month = self._month_number(match.group("end")) if match.group("end") else start_month
        if start_month is None or end_month is None:
            return None
        months = self._month_range(start_month, end_month)
        original_period = match.group(0)
        list_segment = lowered[match.start():].split("?", 1)[0]
        if not match.group("end") and ("," in list_segment or " и " in list_segment):
            listed_months = [
                month for token in re.findall(r"[а-яё]+", list_segment)
                if (month := self._month_number(token)) is not None
            ]
            if len(listed_months) > 1:
                months = list(dict.fromkeys(listed_months))
                original_period = self._format_month_list(months)
        return {
            "original_period": original_period,
            "months": months,
            "year": now_year,
        }

    @staticmethod
    def _matched_season_text(text: str, season: str) -> str:
        forms = {
            "весна": ("весной", "весна"),
            "лето": ("летом", "лето"),
            "осень": ("осенью", "осень"),
            "зима": ("зимой", "зима"),
        }
        return next((form for form in forms[season] if form in text), season)

    @staticmethod
    def _month_number(value: str | None) -> int | None:
        lowered = str(value or "").lower()
        month_prefixes = (
            ("январ", 1), ("феврал", 2), ("март", 3), ("апрел", 4),
            ("ма", 5), ("июн", 6), ("июл", 7), ("август", 8),
            ("сентябр", 9), ("октябр", 10), ("ноябр", 11), ("декабр", 12),
        )
        return next((number for prefix, number in month_prefixes if lowered.startswith(prefix)), None)

    @staticmethod
    def _month_range(start_month: int, end_month: int) -> list[int]:
        months = [start_month]
        while months[-1] != end_month:
            months.append((months[-1] % 12) + 1)
        return months

    @staticmethod
    def _format_month_list(months: list[int]) -> str:
        month_forms = {
            1: "январе", 2: "феврале", 3: "марте", 4: "апреле",
            5: "мае", 6: "июне", 7: "июле", 8: "августе",
            9: "сентябре", 10: "октябре", 11: "ноябре", 12: "декабре",
        }
        names = [month_forms[month] for month in months]
        if len(names) == 1:
            return f"в {names[0]}"
        if len(names) == 2:
            return f"в {names[0]} и {names[1]}"
        return "в " + ", ".join(names[:-1]) + f" и {names[-1]}"

    @staticmethod
    def _weekend_dates_for_period(months: list[int], year: int) -> list[date]:
        dates: list[date] = []
        current_year = year
        previous_month = months[0]
        for index, month in enumerate(months):
            if index and month < previous_month:
                current_year += 1
            current = date(current_year, month, 1)
            while current.month == month:
                if current.weekday() >= 5:
                    dates.append(current)
                current += timedelta(days=1)
            previous_month = month
        return dates

    def _extract_date(self, text: str) -> date | None:
        now = datetime.now(UTC).date()
        now_year = now.year
        relative_date = self._extract_relative_date(text, now)
        if relative_date is not None:
            return relative_date

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
            if self._day_only_match_looks_like_time(text, match.start(), match.end()):
                return None
            day = int(match.group("day"))
            return self._nearest_day_only_date(now, day)

        return None

    @staticmethod
    def _extract_relative_date(text: str, current_date: date) -> date | None:
        lowered = str(text or "").lower()
        if "послезавтра" in lowered:
            return current_date.fromordinal(current_date.toordinal() + 2)
        if "завтра" in lowered:
            return current_date.fromordinal(current_date.toordinal() + 1)
        if "сегодня" in lowered:
            return current_date
        return None

    @staticmethod
    def _day_only_match_looks_like_time(text: str, start: int, end: int) -> bool:
        before = text[max(0, start - 6):start].lower()
        after = text[end:end + 12].lower()
        stripped_after = after.lstrip()
        return stripped_after.startswith(":") or stripped_after.startswith("час") or before.endswith("к ")

    @staticmethod
    def _is_jump_schedule_request(text: str, conversation_context: dict | None = None) -> bool:
        parts = [str(text or "")]
        if isinstance(conversation_context, dict):
            recent_messages = conversation_context.get("recent_messages", [])
            if isinstance(recent_messages, list):
                parts.extend(str(item.get("content") or "") for item in recent_messages if isinstance(item, dict))

        combined = "\n".join(parts).lower()
        jump_markers = ("прыж", "тандем", "полет", "полёт", "аэродром", "инструкт")
        schedule_markers = ("выходн", "суббот", "воскрес", "сегодня", "завтра", "послезавтра", "дата", "когда")
        office_markers = ("офис", "сертифик", "подар")

        if any(marker in combined for marker in jump_markers):
            return True
        if any(marker in combined for marker in office_markers):
            return False
        return any(marker in combined for marker in schedule_markers)

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
