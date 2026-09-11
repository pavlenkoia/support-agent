from __future__ import annotations

import json

import pytest
from tests.evidence_fixtures import migrate_fixture

from app.services import direct_llm as direct_llm_module
from app.services.direct_llm import DirectLLMService


class ActionClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[dict] = []

    def generate(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        return self.response

    def get_last_call_info(self) -> dict:
        return {"provider": "test", "model": "test", "usage": {"prompt_tokens": 1}}


class SequenceActionClient(ActionClient):
    def __init__(self, *responses: str) -> None:
        super().__init__(responses[0])
        self.responses = list(responses)

    def generate(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        return self.responses.pop(0)


class PromptService:
    def load_system_prompt(self) -> str:
        return "Поведенческий контракт агента."

    def render_cannot_answer(self) -> str:
        return "Нет подтверждённых данных."


def test_direct_llm_exposes_only_active_agent_loop_entrypoints() -> None:
    service = DirectLLMService(client=ActionClient('{}'), prompt_service=PromptService())

    for legacy_method in ("answer", "assess_request", "classify_turn", "respond_social"):
        assert not hasattr(service, legacy_method)





def test_begin_turn_requests_wiki_as_the_only_factual_source() -> None:
    client = ActionClient('{"_native_tool_calls": [{"id": "fixture-native", "type": "function", "function": {"name": "wiki_lookup", "arguments": "{\\"query\\": \\"вопрос о чате\\", \\"context_scope\\": \\"текущий вопрос\\", \\"needed_fact\\": \\"способ решения\\"}"}}]}')
    service = DirectLLMService(client=client, prompt_service=PromptService())

    result = service.begin_turn(
        text="Здравствуйте, так и не добавили в чат",
        context={"recent_messages": []},
    )

    assert result["kind"] == "wiki_lookup"
    assert "response_text" not in result
    prompt = str(client.calls[0]["user_prompt"])
    assert "calendar_lookup — не источник бизнес-фактов" in prompt
    assert "wiki_lookup — единственный источник бизнес-фактов из Wiki." in prompt
    assert "Не вводи фиксированную последовательность инструментов" in prompt
    assert "отдельного финализатора" in prompt
    assert "Не утверждай, что выполнил действие" in prompt
    assert "profile_context" in json.loads(prompt)
    assert "Любой неизвестный инструмент" in prompt
    assert client.calls[0]["tool_choice"] == "auto"
    assert client.calls[0]["parallel_tool_calls"] is False
    assert "response_format" not in client.calls[0]
    assert client.calls[0]["tools"][0]["function"]["name"] == "wiki_lookup"



def test_native_tool_descriptions_keep_calendar_facts_separate_from_wiki_facts() -> None:
    tools = {tool["function"]["name"]: tool["function"]["description"] for tool in DirectLLMService._native_tools()}

    assert "not determinable by calendar" in tools["wiki_lookup"]
    assert "current customer message" in tools["calendar_lookup"]


def test_begin_turn_accepts_provider_native_tool_call_with_valid_arguments() -> None:
    client = ActionClient(
        '{"_native_tool_calls":[{"id":"native-wiki","function":{"name":"wiki_lookup","arguments":"{\\\"query\\\":\\\"вопрос\\\",\\\"context_scope\\\":\\\"контекст\\\",\\\"needed_fact\\\":\\\"факт\\\"}"}}]}'
    )
    service = DirectLLMService(client=client, prompt_service=PromptService())

    result = service.begin_turn(text="Содержательный вопрос", context={"recent_messages": []})

    assert result["kind"] == "wiki_lookup"




def test_begin_turn_accepts_provider_tool_name_with_serialized_content_suffix_in_followup() -> None:
    client = ActionClient(
        '{"_native_tool_calls":[{"id":"native-wiki-1","function":{"name":"wiki_lookup","arguments":"{\\\"query\\\":\\\"стоимость\\\",\\\"context_scope\\\":\\\"самостоятельный вариант\\\",\\\"needed_fact\\\":\\\"цена\\\"}"}}]}'
    )
    service = DirectLLMService(client=client, prompt_service=PromptService())

    result = service.begin_turn(
        text="Сколько это стоит?",
        context={"recent_messages": [{"role": "user", "content": "У вас есть услуга?"}, {"role": "assistant", "content": "Да."}]},
    )

    assert result["kind"] == "wiki_lookup"
    assert result["tool_call_id"] == "native-wiki-1"



def test_begin_turn_does_not_guess_an_unknown_malformed_native_tool() -> None:
    client = ActionClient('{"_native_tool_calls":[{"function":{"name":"provider-garbage","arguments":"not-json-and-not-an-envelope"}}]}')
    service = DirectLLMService(client=client, prompt_service=PromptService())

    result = service.begin_turn(
        text="Сколько это стоит?",
        context={"recent_messages": [{"role": "user", "content": "У вас есть услуга?"}, {"role": "assistant", "content": "Да."}]},
    )

    assert result["kind"] == "final"
    assert result["result"]["route"] == "retry_pending"


def test_begin_turn_retries_a_non_json_selector_response_with_fresh_protocol_context() -> None:
    client = SequenceActionClient(
        "Совершенно посторонний свободный текст, не JSON и не tool call.",
        '{"_native_tool_calls":[{"id":"native-wiki-retry","function":{"name":"wiki_lookup","arguments":"{\\"query\\":\\"стоимость прыжка с видео\\",\\"context_scope\\":\\"прыжок с инструктором\\",\\"needed_fact\\":\\"стоимость\\"}"}}]}',
    )
    service = DirectLLMService(client=client, prompt_service=PromptService())

    result = service.begin_turn(text="Сколько стоит прыжок с видео и инструктором?", context={"recent_messages": []})

    assert result["kind"] == "wiki_lookup"
    assert result["tool_call_id"] == "native-wiki-retry"
    assert len(client.calls) == 2
    first_packet = json.loads(str(client.calls[0]["user_prompt"]))
    retry_packet = json.loads(str(client.calls[1]["user_prompt"]))
    assert first_packet["user_message"] == retry_packet["user_message"]
    assert "protocol_recovery" not in first_packet
    assert retry_packet["protocol_recovery"] == "previous_selector_output_was_not_valid_json"


def test_begin_turn_parses_valid_tool_envelope_before_provider_trailing_junk() -> None:
    client = ActionClient('{"_native_tool_calls": [{"id": "fixture-native", "type": "function", "function": {"name": "wiki_lookup", "arguments": "{\\"query\\": \\"вопрос\\", \\"context_scope\\": \\"контекст\\", \\"needed_fact\\": \\"факт\\"}"}}]}```json\\n{"tool_call":"wiki_lookup"}\\n```')
    service = DirectLLMService(client=client, prompt_service=PromptService())

    result = service.begin_turn(text="Содержательный вопрос", context={"recent_messages": []})

    assert result["kind"] == "wiki_lookup"








def test_begin_turn_returns_a_tool_free_social_reply_directly() -> None:
    client = ActionClient('{"route":"social_reply","response_text":"Привет! Чем могу помочь?","confidence":0.9,"reason":"pure_social"}')
    service = DirectLLMService(client=client, prompt_service=PromptService())

    result = service.begin_turn(text="Привет", context={"recent_messages": []})

    assert result["kind"] == "direct_response"
    assert result["result"]["route"] == "social_reply"
    assert result["result"]["response_text"] == "Привет! Чем могу помочь?"


def test_continue_after_tool_hides_completed_tool_from_agent() -> None:
    client = ActionClient('{"route":"answer","response_text":"26 сентября — суббота, выходной.","confidence":0.9,"reason":"calendar_fact"}')
    service = DirectLLMService(client=client, prompt_service=PromptService())

    result = service.continue_after_tool(
        text="Можно ли прыгнуть 25 и 26 сентября?",
        context={"recent_messages": [], "tool_observations": [{
            "tool": "calendar_lookup", "status": "ready",
            "summary": "25.09.2026 — пятница, будний день; 26.09.2026 — суббота, выходной.",
            "structured": {"dates": []},
        }]},
        tool_name="calendar_lookup", tool_call_id="calendar-1",
        tool_request={"date_expressions": ["25 сентября", "26 сентября"], "requested_calendar_fact": "дни недели"},
        observation={"tool": "calendar_lookup", "status": "ready", "summary": "25.09.2026 — пятница, будний день; 26.09.2026 — суббота, выходной.", "structured": {"dates": []}},
    )

    assert result["kind"] == "direct_response"
    assert result["result"]["route"] == "answer"
    assert {tool["function"]["name"] for tool in client.calls[0]["tools"]} == {"wiki_lookup"}
    assert "отдельному финализатору" in client.calls[0]["messages"][-1]["content"]


def test_selector_normalizes_standard_openai_tool_calls_envelope() -> None:
    service = DirectLLMService(client=ActionClient("{}"), prompt_service=PromptService())
    service._active_llm_trace = [{}]

    result = service._selector_decision({
        "tool_calls": [{
            "id": "call-1",
            "type": "function",
            "function": {"name": "wiki_lookup", "arguments": json.dumps({
                "query": "условия прыжка",
                "context_scope": "прыжок с парашютом",
                "needed_fact": "что нужно для прыжка",
            })},
        }],
    })

    assert result["kind"] == "wiki_lookup"
    assert result["tool_call_id"] == "call-1"


@pytest.mark.parametrize("count", [2, 3, 4])
def test_selector_coalesces_any_same_scope_parallel_wiki_calls(count: int) -> None:
    service = DirectLLMService(client=ActionClient("{}"), prompt_service=PromptService())
    service._active_llm_trace = [{}]
    calls = []
    for index in range(count):
        calls.append({"id": f"wiki-{index}", "type": "function", "function": {"name": "wiki_lookup", "arguments": json.dumps({
            "query": f"часть запроса {index + 1}",
            "context_scope": "прыжок с инструктором",
            "needed_fact": f"сведение {index + 1}",
        })}})

    result = service._selector_decision({"_native_tool_calls": calls})

    assert result["kind"] == "wiki_lookup"
    assert result["tool_call_id"] == "wiki-0"
    for index in range(count):
        assert f"часть запроса {index + 1}" in result["tool_request"]["query"]
        assert f"сведение {index + 1}" in result["tool_request"]["needed_fact"]
    assert service._active_llm_trace[-1]["provider_protocol_normalization"] == "coalesced_parallel_wiki_calls"


def test_selector_normalizes_legacy_provider_single_tool_map_list() -> None:
    service = DirectLLMService(client=ActionClient("{}"), prompt_service=PromptService())
    service._active_llm_trace = [{}]

    result = service._selector_decision([{
        "wiki_lookup": {
            "query": "требования для прыжка с парашютом",
            "context_scope": "хочу прыгнуть с парашютом",
            "needed_fact": "что необходимо для первого прыжка",
        },
    }])

    assert result["kind"] == "wiki_lookup"
    assert result["tool_request"] == {
        "query": "требования для прыжка с парашютом",
        "context_scope": "хочу прыгнуть с парашютом",
        "needed_fact": "что необходимо для первого прыжка",
    }
    assert result["tool_call_id"] == "compat-tool-call-1"


def test_begin_turn_prompt_requires_agent_owned_response() -> None:
    client = ActionClient('{"action":"finalize","response_intent":"social_reply","reason":"social"}')
    service = DirectLLMService(client=client, prompt_service=PromptService())

    service.begin_turn(text="Спасибо", context={"recent_messages": []})

    prompt = str(client.calls[0]["user_prompt"])
    assert '"response_schema"' in prompt
    assert "Не возвращай action=finalize" in prompt
    assert "Если сведения инструментов не нужны или уже достаточны" in prompt

def test_continue_after_tool_prompt_requires_agent_owned_response() -> None:
    client = ActionClient('{"route":"answer","response_text":"Готово.","confidence":1,"reason":"done"}')
    service = DirectLLMService(client=client, prompt_service=PromptService())

    service.continue_after_tool(
        text="Вопрос",
        context={"recent_messages": []},
        tool_name="wiki_lookup",
        tool_call_id="expected-id",
        tool_request={"query": "вопрос", "context_scope": "контекст", "needed_fact": "факт"},
        observation={"status": "ready", "summary": "Факт"},
    )

    prompt = str(client.calls[0]["messages"][-1]["content"])
    assert 'agent loop' in prompt
    assert 'готовый содержательный JSON-ответ' in prompt
    assert 'отдельному финализатору' in prompt


def test_malformed_json_retries_once_and_records_both_provider_calls() -> None:
    class MalformedClient:
        def __init__(self) -> None:
            self.calls = 0

        def generate(self, **kwargs: object) -> str:
            self.calls += 1
            _ = kwargs
            return "{not-json"

        def get_last_call_info(self) -> dict:
            return {"provider": "test", "model": "test", "duration_ms": 11, "attempts": 1, "usage": {"prompt_tokens": 1}}

    service = DirectLLMService(client=MalformedClient(), prompt_service=PromptService())

    result = service.begin_turn(text="Содержательный вопрос", context={"recent_messages": []})

    assert result["kind"] == "final"
    assert result["result"]["route"] == "retry_pending"
    assert len(result["llm_trace"]) == 2
    assert all(item["entry_kind"] == "model_call" for item in result["llm_trace"])
    assert all(item["input_packet"]["status"] == "complete" for item in result["llm_trace"])
    assert result["llm_trace"][0]["input_packet"]["data"]["user_message"] == "Содержательный вопрос"
    assert "protocol_recovery" not in result["llm_trace"][0]["input_packet"]["data"]
    # The retry marker is sent to the provider but intentionally excluded from
    # the customer-safe trace packet.
    assert "protocol_recovery" not in result["llm_trace"][1]["input_packet"]["data"]




def test_begin_turn_retries_invalid_native_tool_envelope_once() -> None:
    client = SequenceActionClient(
        '{"_native_tool_calls":[{"id":"bad","function":{"name":"calendar_lookup","arguments":"{\\"date_expressions\\":[\\"25 сентября\\"],\\"requested_calendar_fact\\":\\"день недели\\",\\"extra\\":true}"}}]}',
        '{"_native_tool_calls":[{"id":"calendar-ok","function":{"name":"calendar_lookup","arguments":"{\\"date_expressions\\":[\\"25 сентября\\",\\"26 сентября\\"],\\"requested_calendar_fact\\":\\"дни недели\\"}"}}]}',
    )
    service = DirectLLMService(client=client, prompt_service=PromptService())

    result = service.begin_turn(text="Можно ли прыгнуть 25 и 26 сентября?", context={"recent_messages": []})

    assert result["kind"] == "calendar_lookup"
    assert result["tool_call_id"] == "calendar-ok"
    assert len(client.calls) == 2
    retry_packet = json.loads(str(client.calls[1]["user_prompt"]))
    assert retry_packet["protocol_recovery"] == "previous_selector_output_violated_native_protocol"


def test_selector_packet_includes_declared_profile_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    class ProfilePolicy:
        def load_profile(self) -> dict:
            return {"role": "declared role", "scope": {"in_scope": ["declared service"], "out_of_scope": ["other service"]}}

    monkeypatch.setattr(direct_llm_module, "PolicyService", ProfilePolicy)
    service = DirectLLMService(client=ActionClient("{}"), prompt_service=PromptService())

    packet = json.loads(service._build_selector_prompt(text="question", context={"recent_messages": []}))

    assert packet["profile_context"] == {"role": "declared role", "in_scope": ["declared service"], "out_of_scope": ["other service"]}


def test_continue_after_tool_retries_invalid_terminal_envelope_once() -> None:
    client = SequenceActionClient(
        "not-json",
        '{"route":"answer","response_text":"Подтверждённый ответ.","confidence":0.9,"reason":"grounded"}',
    )
    service = DirectLLMService(client=client, prompt_service=PromptService())
    context = {"recent_messages": [], "tool_observations": [
        {"tool": "calendar_lookup", "status": "ready", "summary": "calendar"},
        {"tool": "wiki_lookup", "status": "ready", "source_refs": ["kb/page.md"]},
        {"tool": "wiki_lookup", "status": "not_found", "source_refs": []},
    ]}

    result = service.continue_after_tool(
        text="Question", context=context, tool_name="wiki_lookup", tool_call_id="call-3",
        tool_request={"query": "Question", "context_scope": "Question", "needed_fact": "fact"},
        observation={"tool": "wiki_lookup", "status": "not_found", "source_refs": [], "grounded_facts": []},
    )

    assert result["kind"] == "direct_response"
    assert result["result"]["response_text"] == "Подтверждённый ответ."
    assert len(client.calls) == 2
    assert "protocol_recovery" in client.calls[1]["messages"][-1]["content"]
