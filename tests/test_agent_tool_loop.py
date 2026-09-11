from __future__ import annotations
from tests.evidence_fixtures import migrate_fixture

from app.services.agent_tool_loop import (
    CalendarLookupTool,
    UnifiedTurnService,
    WikiLookupTool,
)
from app.services.tool_runtime import ToolRuntimeService


class RecordingCalendarLookup:
    def lookup(self, *, text: str, context: dict, tool_request: dict[str, str]) -> dict:
        _ = (text, context, tool_request)
        return {"status": "not_found", "summary": "", "structured": {}}


class RecordingWikiLookup:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def lookup(self, *, text: str, context: dict, tool_request: dict[str, str]) -> dict:
        self.calls.append({"text": text, "context": context, "tool_request": tool_request})
        return migrate_fixture({
            "grounding_status": "ready",
            "grounded_facts": ["Запись на тандем доступна через форму."],
            "answer_basis": "Предложить форму записи на тандем.",
            "source_refs": ["compiled/concepts/booking.md"],
        })




def test_unified_turn_runs_wiki_after_model_requests_its_tool() -> None:
    class TurnModel:
        def begin_turn(self, *, text: str, context: dict) -> dict:
            _ = (text, context)
            return {"kind": "wiki_lookup", "tool_call_id": "wiki-1", "tool_request": {"query": "запись", "context_scope": "тандем", "needed_fact": "канал"}, "llm_trace": [{"role": "direct_llm", "step": "customer_turn"}]}

        def continue_after_tool(self, **kwargs) -> dict:
            return {"kind": "finalization_requested", "response_intent": "answer", "reason": "wiki_ready", "llm_trace": []}

    wiki = RecordingWikiLookup()
    turn = UnifiedTurnService(model=TurnModel(), wiki_lookup=wiki, calendar_lookup=RecordingCalendarLookup())

    result = turn.run(text="Как записаться на тандем?", context={})

    assert len(wiki.calls) == 1
    assert result["kb_result"]["source_refs"] == ["compiled/concepts/booking.md"]
    assert result["trace"]["actions"] == ["wiki_lookup"]


def test_unified_turn_keeps_semantic_answer_in_agent_after_repeated_tools() -> None:
    class TurnModel:
        def __init__(self) -> None:
            self.step = 0

        def begin_turn(self, *, text: str, context: dict) -> dict:
            _ = (text, context)
            self.step += 1
            if self.step < 3:
                return {
                    "kind": "wiki_lookup", "tool_call_id": f"wiki-{self.step}",
                    "tool_request": {"query": f"часть {self.step}", "context_scope": "прыжки", "needed_fact": "условие"},
                    "llm_trace": [],
                }
            return {
                "kind": "direct_response",
                "result": {"route": "answer", "response_text": "Готовый ответ агента.", "confidence": 0.9, "reason": "facts_collected"},
                "llm_trace": [],
            }

    wiki = RecordingWikiLookup()
    result = UnifiedTurnService(model=TurnModel(), wiki_lookup=wiki, calendar_lookup=RecordingCalendarLookup()).run(
        text="Вопрос", context={"recent_messages": []},
    )

    assert len(wiki.calls) == 2
    assert result["trace"]["actions"] == ["wiki_lookup", "wiki_lookup"]
    assert result["final_result"]["response_text"] == "Готовый ответ агента."
    assert "finalization_requested" not in result


def test_agent_loop_owns_calendar_and_wiki_answer_without_second_writer() -> None:
    class Model:
        def __init__(self) -> None:
            self.step = 0

        def begin_turn(self, **kwargs) -> dict:
            self.step += 1
            if self.step == 1:
                return {"kind": "calendar_lookup", "tool_call_id": "calendar-1", "tool_request": {
                    "date_expressions": ["25 сентября", "26 сентября"], "requested_calendar_fact": "дни недели",
                }, "llm_trace": []}
            if self.step == 2:
                return {"kind": "wiki_lookup", "tool_call_id": "wiki-1", "tool_request": {
                    "query": "обычные прыжки в выходные", "context_scope": "прыжки 25 и 26 сентября", "needed_fact": "правило буднего дня и выходного",
                }, "llm_trace": []}
            return {"kind": "direct_response", "result": {
                "route": "answer",
                "response_text": "25 сентября — пятница: только по отдельной договорённости для группы. 26 сентября — суббота: обычные прыжки возможны при анонсе и подходящей погоде.",
                "confidence": 0.9, "reason": "calendar_and_wiki_facts",
            }, "llm_trace": []}

    class Calendar:
        def lookup(self, **kwargs) -> dict:
            return {"status": "ready", "summary": "25.09.2026 — пятница, будний день; 26.09.2026 — суббота, выходной.", "structured": {"dates": []}}

    class Wiki:
        def lookup(self, **kwargs) -> dict:
            return migrate_fixture({"grounding_status": "ready", "grounded_facts": ["Обычные прыжки проходят по выходным."], "answer_basis": "Расписание обычных прыжков."})

    result = UnifiedTurnService(model=Model(), wiki_lookup=Wiki(), calendar_lookup=Calendar()).run(
        text="можно ли прыгнуть 25 и 26 сентября?", context={"recent_messages": []},
    )

    assert result["trace"]["actions"] == ["calendar_lookup", "wiki_lookup"]
    assert result["final_result"]["route"] == "answer"
    assert "26 сентября — суббота" in result["final_result"]["response_text"]
    assert "втор" not in result["final_result"]["response_text"].casefold()






def test_unified_turn_runs_calendar_after_model_requests_its_tool() -> None:
    class TurnModel:
        def __init__(self) -> None:
            self.calls = 0

        def begin_turn(self, *, text: str, context: dict) -> dict:
            _ = (text, context)
            self.calls += 1
            if self.calls == 2:
                return {"kind": "finalization_requested", "response_intent": "answer", "reason": "calendar", "llm_trace": []}
            return {
                "kind": "calendar_lookup",
                "tool_call_id": "calendar-1",
                "tool_request": {"date_expression": "сегодня", "requested_calendar_fact": "день недели"},
                "llm_trace": [{"role": "direct_llm", "step": "customer_turn"}],
            }

    class RecordingCalendarLookup:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def lookup(self, *, text: str, context: dict, tool_request: dict[str, str]) -> dict:
            self.calls.append({"text": text, "context": context, "tool_request": tool_request})
            return {
                "status": "ready",
                "summary": "Сегодня — понедельник.",
                "structured": {"iso_date": "2026-08-31", "weekday_ru": "понедельник", "is_weekend": False},
            }

    calendar = RecordingCalendarLookup()
    turn = UnifiedTurnService(model=TurnModel(), wiki_lookup=RecordingWikiLookup(), calendar_lookup=calendar)

    result = turn.run(text="Офис сегодня работает?", context={"calendar_reference_at": "2026-08-31T08:00:00+00:00"})

    assert calendar.calls == [{
        "text": "Офис сегодня работает?",
        "context": {"calendar_reference_at": "2026-08-31T08:00:00+00:00", "tool_observations": []},
        "tool_request": {"date_expression": "сегодня", "requested_calendar_fact": "день недели"},
    }]
    assert result["trace"]["actions"] == ["calendar_lookup"]
    assert result["tool_observations"] == [{
        "tool": "calendar_lookup",
        "status": "ready",
        "summary": "Сегодня — понедельник.",
        "structured": {"iso_date": "2026-08-31", "weekday_ru": "понедельник", "is_weekend": False},
    }]




def test_calendar_lookup_tool_resolves_relative_date_against_message_context() -> None:
    tool = CalendarLookupTool(runtime=ToolRuntimeService())

    result = tool.lookup(
        text="Офис сегодня работает?",
        context={"calendar_reference_at": "2026-08-31T08:00:00+00:00"},
        tool_request={"date_expression": "сегодня", "requested_calendar_fact": "день недели"},
    )

    assert result == {
        "status": "ready",
        "summary": "31.08.2026 — понедельник, будний день.",
        "structured": {"dates": [{
            "iso_date": "2026-08-31",
            "weekday_ru": "понедельник",
            "is_weekend": False,
            "year": 2026,
        }]},
    }


def test_calendar_lookup_tool_interprets_reference_in_operational_timezone() -> None:
    tool = CalendarLookupTool(runtime=ToolRuntimeService())

    result = tool.lookup(
        text="Офис сегодня работает?",
        context={"calendar_reference_at": "2026-08-30T20:00:00+00:00"},
        tool_request={"date_expression": "сегодня", "requested_calendar_fact": "день недели"},
    )

    assert result["structured"]["dates"][0]["iso_date"] == "2026-08-31"
    assert result["structured"]["dates"][0]["weekday_ru"] == "понедельник"


def test_unified_turn_keeps_wiki_llm_trace_for_operational_audit() -> None:
    class TurnModel:
        def begin_turn(self, *, text: str, context: dict) -> dict:
            _ = (text, context)
            return {"kind": "wiki_lookup", "tool_call_id": "wiki-1", "tool_request": {"query": "запись", "context_scope": "тандем", "needed_fact": "канал"}, "llm_trace": [{"role": "direct_llm", "step": "customer_turn"}]}

    class RetryingWikiLookup:
        def lookup(self, *, text: str, context: dict, tool_request: dict[str, str]) -> dict:
            _ = (text, context, tool_request)
            return {
                "grounding_status": "llm_unavailable",
                "source_refs": [],
                "trace": {
                    "llm_trace": [
                        {"role": "kb_agent", "step": "grounded_extraction", "attempts": 4, "error": "TimeoutError"}
                    ]
                },
            }

    turn = UnifiedTurnService(model=TurnModel(), wiki_lookup=RetryingWikiLookup(), calendar_lookup=RecordingCalendarLookup())

    result = turn.run(text="Вопрос", context={})

    assert result["llm_trace"] == [
        {"role": "direct_llm", "step": "customer_turn"},
        {"role": "kb_agent", "step": "grounded_extraction", "attempts": 4, "error": "TimeoutError"},
    ]


def test_wiki_lookup_tool_preserves_existing_catalog_reader_contract() -> None:
    class Retrieval:
        def retrieve(self, *args: object, **kwargs: object) -> dict:
            return {
                "kb_architecture": "llm_wiki",
                "kb_mode": "llm_wiki_catalog",
                "kb_snippets": [{"source_ref": "index/catalog.json"}],
            }

    class Reader:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def read(self, text: str, hits: list[dict], **kwargs: object) -> dict:
            self.calls.append({"text": text, "hits": hits, **kwargs})
            return {"grounding_status": "ready", "source_refs": ["compiled/concepts/booking.md"]}

    reader = Reader()
    tool = WikiLookupTool(
        retrieval=Retrieval(),
        kb_agent=reader,
        knowledge_backend="filesystem",
        knowledge_root="/tmp/wiki",
    )

    result = tool.lookup(
        text="Как записаться?",
        context={"recent_messages": []},
        tool_request={"query": "запись на самостоятельный прыжок", "context_scope": "самостоятельный прыжок", "needed_fact": "канал записи"},
    )

    assert result["source_refs"] == ["compiled/concepts/booking.md"]
    assert reader.calls == [{
        "text": "запись на самостоятельный прыжок",
        "hits": [{"source_ref": "index/catalog.json"}],
        "conversation_context": {"user_question": "Как записаться?", "recent_messages": []},
        "require_coverage_review": True,
    }]
