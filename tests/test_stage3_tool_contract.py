from __future__ import annotations
from tests.evidence_fixtures import migrate_fixture

from app.services.agent_tool_loop import UnifiedTurnService
from app.services.direct_llm import DirectLLMService


class Stage3RecordingClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls: list[dict] = []
        self.index = 0

    def generate(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        response = self.responses[self.index]
        self.index += 1
        return response

    def get_last_call_info(self) -> dict:
        return {"provider": "test", "model": "test", "usage": {"prompt_tokens": 1}}


class PromptService:
    def load_system_prompt(self) -> str:
        return "Нейтральный тестовый профиль."


class RecordingClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[dict] = []

    def generate(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        return self.response

    def get_last_call_info(self) -> dict:
        return {"provider": "test", "model": "test", "usage": {"prompt_tokens": 1}}


class RecordingWiki:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def lookup(self, *, text: str, context: dict, tool_request: dict[str, str]) -> dict:
        self.calls.append({"text": text, "context": context, "tool_request": tool_request})
        return migrate_fixture({"grounding_status": "ready", "grounded_facts": ["Факт"], "answer_basis": "Факт"})


class PartialRecordingWiki(RecordingWiki):
    """Ready Wiki result without complete coverage, so continuation is required."""

    def lookup(self, *, text: str, context: dict, tool_request: dict[str, str]) -> dict:
        self.calls.append({"text": text, "context": context, "tool_request": tool_request})
        return migrate_fixture({
            "grounding_status": "ready",
            "grounded_facts": ["Частичный факт"],
            "answer_basis": "Частичный факт",
            "coverage": {
                "status": "partial",
                "answered_parts": [{"question_part": "Подтверждённая часть", "fact_ids": ["f1"]}],
                "missing_parts": ["Недостающий факт"],
                "conflicts": [],
                "unresolved_constraints": [],
            },
        })


class RecordingCalendar:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def lookup(self, *, text: str, context: dict, tool_request: dict[str, str]) -> dict:
        self.calls.append({"text": text, "context": context, "tool_request": tool_request})
        return {"status": "ready", "summary": "Сегодня понедельник.", "structured": {"weekday_ru": "понедельник"}}


def test_begin_turn_accepts_action_finalize_without_customer_text() -> None:
    client = RecordingClient('{"action":"finalize","response_intent":"social_reply","reason":"done"}')
    service = DirectLLMService(client=client, prompt_service=PromptService())

    result = service.begin_turn(text="Спасибо", context={"recent_messages": []})

    assert result["kind"] == "finalization_requested"
    assert result["response_intent"] == "social_reply"
    assert client.calls[0]["tool_choice"] == "auto"
    prompt = client.calls[0]["user_prompt"]
    assert '"action": "finalize"' in prompt
    assert '"response_intent": "answer|social_reply|clarification|missing_grounding"' in prompt


def test_continue_after_tool_provides_native_tools_to_model() -> None:
    client = RecordingClient('{"action":"finalize","response_intent":"answer","reason":"done"}')
    service = DirectLLMService(client=client, prompt_service=PromptService())

    result = service.continue_after_tool(
        text="Вопрос",
        context={"recent_messages": []},
        tool_name="wiki_lookup",
        tool_call_id="expected-id",
        tool_request={"query": "вопрос", "context_scope": "контекст", "needed_fact": "факт"},
        observation={"status": "ready", "summary": "Факт"},
    )

    assert result["kind"] == "finalization_requested"
    assert result["response_intent"] == "answer"
    assert [t["function"]["name"] for t in client.calls[0]["tools"]] == ["calendar_lookup"]
    assert client.calls[0]["parallel_tool_calls"] is False


def test_unified_turn_stops_after_third_tool_request() -> None:
    class LoopModel:
        def __init__(self) -> None:
            self.calls = 0

        def begin_turn(self, *, text: str, context: dict) -> dict:
            self.calls += 1
            if self.calls == 1:
                return {"kind": "wiki_lookup", "tool_request": {"query": "q1", "context_scope": "s1", "needed_fact": "f1"}, "tool_call_id": "w1", "llm_trace": []}
            if self.calls == 2:
                return {"kind": "calendar_lookup", "tool_request": {"date_expression": "today", "requested_calendar_fact": "weekday"}, "tool_call_id": "c1", "llm_trace": []}
            return {"kind": "wiki_lookup", "tool_request": {"query": "q2", "context_scope": "s2", "needed_fact": "f2"}, "tool_call_id": "w2", "llm_trace": []}

    turn = UnifiedTurnService(model=LoopModel(), wiki_lookup=PartialRecordingWiki(), calendar_lookup=RecordingCalendar())
    result = turn.run(text="Вопрос", context={"recent_messages": []})

    assert result["final_result"]["reason"] == "tool_budget_exceeded"
    assert result["final_result"]["response_text"] == ""
    assert result["trace"]["actions"] == ["wiki_lookup", "calendar_lookup"]
    assert len(result["tool_requests"]) == 2


def test_unified_turn_returns_finalization_requested_after_calendar_then_wiki() -> None:
    class LoopModel:
        def __init__(self) -> None:
            self.calls = 0

        def begin_turn(self, *, text: str, context: dict) -> dict:
            self.calls += 1
            if self.calls == 1:
                return {"kind": "calendar_lookup", "tool_request": {"date_expression": "сегодня", "requested_calendar_fact": "день недели"}, "tool_call_id": "calendar-1", "llm_trace": [{"role": "direct_llm", "step": "customer_turn"}]}
            if self.calls == 2:
                return {"kind": "wiki_lookup", "tool_request": {"query": "q", "context_scope": "s", "needed_fact": "f"}, "tool_call_id": "wiki-1", "llm_trace": []}
            return {"kind": "finalization_requested", "response_intent": "answer", "reason": "need_response", "llm_trace": [{"role": "direct_llm", "step": "tool_result_selection"}]}

    class Wiki:
        def __init__(self) -> None:
            self.calls = []

        def lookup(self, *, text: str, context: dict, tool_request: dict[str, str]) -> dict:
            self.calls.append({"text": text, "context": context, "tool_request": tool_request})
            return migrate_fixture({"grounding_status": "ready", "grounded_facts": ["Факт"], "answer_basis": "Факт", "source_refs": ["wiki/ref.md"]})

    result = UnifiedTurnService(model=LoopModel(), wiki_lookup=Wiki(), calendar_lookup=RecordingCalendar()).run(text="Вопрос", context={"recent_messages": []})

    assert result["finalization_requested"]["response_intent"] == "answer"
    assert [fact["text"] for fact in result["kb_result"]["grounded_facts"]] == ["Факт"]
    assert result["tool_observations"][0]["status"] == "ready"
    assert result["trace"]["actions"] == ["calendar_lookup", "wiki_lookup"]


def test_direct_llm_continue_after_tool_keeps_all_messages_and_tool_trace_once() -> None:
    client = Stage3RecordingClient([
        '{"action":"finalize","response_intent":"answer","reason":"done"}'
    ])
    service = DirectLLMService(client=client, prompt_service=PromptService())

    result = service.continue_after_tool(
        text="Вопрос",
        context={
            "recent_messages": [{"role": "user", "content": "Привет"}],
            "tool_observations": [
                {"tool": "wiki_lookup", "status": "ready", "summary": "Факт"},
                {"tool": "calendar_lookup", "status": "ready", "summary": "Сегодня понедельник."},
            ],
        },
        tool_name="calendar_lookup",
        tool_call_id="expected-id",
        tool_request={"date_expression": "сегодня", "requested_calendar_fact": "день недели"},
        observation={"status": "ready", "summary": "Сегодня понедельник."},
    )

    assert result["kind"] == "finalization_requested"
    model_calls = [entry for entry in result["llm_trace"] if entry.get("entry_kind") == "model_call"]
    assert len(model_calls) == 1
    assert model_calls[0]["step"] == "tool_result_selection"
    messages = client.calls[0]["messages"]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "tool", "user"]
    assert messages[1]["content"].count("Привет") == 1
    assert messages[1]["content"].count("Факт") == 1
