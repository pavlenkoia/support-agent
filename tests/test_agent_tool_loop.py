from __future__ import annotations

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
        return {
            "grounding_status": "ready",
            "grounded_facts": ["Запись на тандем доступна через форму."],
            "answer_basis": "Предложить форму записи на тандем.",
            "source_refs": ["compiled/concepts/booking.md"],
        }


def test_unified_turn_returns_social_customer_text_without_wiki_lookup() -> None:
    class TurnModel:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def begin_turn(self, *, text: str, context: dict) -> dict:
            self.calls.append({"text": text, "context": context})
            return {
                "kind": "finalization_requested",
                "response_intent": "social_reply",
                "reason": "social_reply",
                "llm_trace": [{"role": "direct_llm", "step": "customer_turn"}],
            }

    model = TurnModel()
    wiki = RecordingWikiLookup()
    turn = UnifiedTurnService(model=model, wiki_lookup=wiki, calendar_lookup=RecordingCalendarLookup())

    result = turn.run(text="Спасибо", context={"recent_messages": []})

    assert model.calls == [{"text": "Спасибо", "context": {"recent_messages": []}}]
    assert wiki.calls == []
    assert result["finalization_requested"]["response_intent"] == "social_reply"
    assert result["trace"]["actions"] == []


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


def test_unified_turn_returns_final_response_after_calendar_observation() -> None:
    class TurnModel:
        def __init__(self) -> None:
            self.contexts: list[dict] = []

        def begin_turn(self, *, text: str, context: dict) -> dict:
            self.contexts.append(context)
            if len(self.contexts) == 1:
                return {
                    "kind": "calendar_lookup",
                "tool_call_id": "calendar-1",
                    "tool_request": {"date_expression": "послезавтра", "requested_calendar_fact": "день недели"},
                    "llm_trace": [],
                }
            return {
                "kind": "finalization_requested",
                "response_intent": "answer",
                "reason": "calendar_evidence",
                "llm_trace": [],
            }

    class Calendar:
        def lookup(self, *, text: str, context: dict, tool_request: dict[str, str]) -> dict:
            _ = (text, context, tool_request)
            return {"status": "ready", "summary": "Дата 2026-09-02 приходится на среда.", "structured": {"weekday_ru": "среда"}}

    model = TurnModel()
    result = UnifiedTurnService(model=model, wiki_lookup=RecordingWikiLookup(), calendar_lookup=Calendar()).run(
        text="Какой день недели будет послезавтра?", context={"recent_messages": []}
    )

    assert len(model.contexts) == 2
    assert model.contexts[1]["tool_observations"] == [{
        "tool": "calendar_lookup",
        "status": "ready",
        "summary": "Дата 2026-09-02 приходится на среда.",
        "structured": {"weekday_ru": "среда"},
    }]
    assert result["finalization_requested"]["response_intent"] == "answer"
    assert result["trace"]["actions"] == ["calendar_lookup"]


def test_calendar_lookup_tool_resolves_relative_date_against_message_context() -> None:
    tool = CalendarLookupTool(runtime=ToolRuntimeService())

    result = tool.lookup(
        text="Офис сегодня работает?",
        context={"calendar_reference_at": "2026-08-31T08:00:00+00:00"},
        tool_request={"date_expression": "сегодня", "requested_calendar_fact": "день недели"},
    )

    assert result == {
        "status": "ready",
        "summary": "Дата 2026-08-31 приходится на понедельник.",
        "structured": {
            "iso_date": "2026-08-31",
            "weekday_ru": "понедельник",
            "is_weekend": False,
            "year": 2026,
        },
    }


def test_calendar_lookup_tool_interprets_reference_in_operational_timezone() -> None:
    tool = CalendarLookupTool(runtime=ToolRuntimeService())

    result = tool.lookup(
        text="Офис сегодня работает?",
        context={"calendar_reference_at": "2026-08-30T20:00:00+00:00"},
        tool_request={"date_expression": "сегодня", "requested_calendar_fact": "день недели"},
    )

    assert result["structured"]["iso_date"] == "2026-08-31"
    assert result["structured"]["weekday_ru"] == "понедельник"


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
        "conversation_context": {"recent_messages": [], "tool_request": {"query": "запись на самостоятельный прыжок", "context_scope": "самостоятельный прыжок", "needed_fact": "канал записи"}},
        "require_coverage_review": True,
    }]
