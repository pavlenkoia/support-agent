import json

import pytest

from app.services.agent_tool_loop import UnifiedTurnService
from app.services.direct_llm import DirectLLMService
from tests.test_stage3_tool_contract import (
    PromptService,
    RecordingCalendar,
    RecordingWiki,
    Stage3RecordingClient,
)

ARGS = {'wiki_lookup': {'query': 'q', 'context_scope': 's', 'needed_fact': 'f'},
        'calendar_lookup': {'date_expression': '2026-09-07', 'requested_calendar_fact': 'weekday'}}
FINAL = {'action': 'finalize', 'response_intent': 'answer', 'reason': 'done'}


def native(name, ident):
    return {'_native_tool_calls': [{'id': ident, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(ARGS[name])}}]}


@pytest.mark.parametrize('order', [('wiki_lookup', 'calendar_lookup'), ('calendar_lookup', 'wiki_lookup')])
def test_real_selector_preserves_both_tools_and_native_history(order):
    client = Stage3RecordingClient([json.dumps(native(name, f'id-{i}')) for i, name in enumerate(order)] + [json.dumps(FINAL)])
    model = DirectLLMService(client=client, prompt_service=PromptService())
    result = UnifiedTurnService(model=model, wiki_lookup=RecordingWiki(), calendar_lookup=RecordingCalendar()).run(text='Вопрос', context={})
    assert result.get('finalization_requested', {}).get('response_intent') == 'answer'
    assert result['kb_result']['grounding_status'] == 'ready'
    assert [o['tool'] for o in result['tool_observations']] == list(order)
    assert len(result['llm_trace']) == 3
    assert [r['tool_call_id'] for r in result['tool_requests']] == ['id-0', 'id-1']
    # Spent capabilities must not be offered again, including the terminal
    # selection where no tool budget remains. The model still chooses finalize.
    assert [t['function']['name'] for t in client.calls[1]['tools']] == [order[1]]
    assert not client.calls[2].get('tools')
    messages = client.calls[2]['messages']
    assert json.loads(messages[1]['content'])['rules'] == json.loads(client.calls[0]['user_prompt'])['rules']
    initial = json.loads(client.calls[0]['user_prompt'])
    middle = json.loads(client.calls[1]['messages'][1]['content'])
    terminal = json.loads(messages[1]['content'])
    assert initial['tool_state'] == {'wiki_lookup': 'not_started', 'calendar_lookup': 'not_started'}
    assert middle['tool_state'] == {order[0]: 'ready', order[1]: 'not_started'}
    assert middle['remaining_tool_calls'] == 1
    assert terminal['remaining_tool_calls'] == 0
    assert [m['role'] for m in messages] == ['system', 'user']
    assert [item['name'] for item in terminal['tool_exchanges']] == list(order)
    assert [item['tool_call_id'] for item in terminal['tool_exchanges']] == ['id-0', 'id-1']
    schema = json.loads(messages[-1]['content'])['required_json_schema']
    assert schema['action'] == 'finalize'
    assert set(schema) == {'action', 'response_intent', 'reason'}
    assert [t['native_tool_call_id'] for t in result['llm_trace'][:2]] == ['id-0', 'id-1']


@pytest.mark.parametrize('extra', [{'response_text': 'draft'}, {'reason': {}}, {'route': 'answer'}, {'confidence': 1}])
def test_finalize_rejects_extra_customer_fields_and_invalid_reason(extra):
    model = DirectLLMService(client=Stage3RecordingClient([json.dumps({**FINAL, **extra})]), prompt_service=PromptService())
    result = model.begin_turn(text='Вопрос', context={})
    assert result['kind'] == 'final'
    assert result['result']['route'] == 'retry_pending'


@pytest.mark.parametrize('ident', ['', None, 7])
def test_missing_or_invalid_native_id_never_executes(ident):
    wiki = RecordingWiki()
    model = DirectLLMService(client=Stage3RecordingClient([json.dumps(native('wiki_lookup', ident))]), prompt_service=PromptService())
    result = UnifiedTurnService(model=model, wiki_lookup=wiki, calendar_lookup=RecordingCalendar()).run(text='Вопрос', context={})
    assert wiki.calls == []
    assert result['final_result']['route'] == 'retry_pending'


def test_parallel_call_cannot_hide_an_unknown_second_call():
    raw = native('wiki_lookup', 'id')
    raw['_native_tool_calls'].append({'id': 'bad', 'function': {'name': 'unknown', 'arguments': '{}'}})
    wiki = RecordingWiki()
    model = DirectLLMService(client=Stage3RecordingClient([json.dumps(raw)]), prompt_service=PromptService())
    result = UnifiedTurnService(model=model, wiki_lookup=wiki, calendar_lookup=RecordingCalendar()).run(text='Вопрос', context={})
    assert wiki.calls == []
    assert result['final_result']['route'] == 'retry_pending'


def test_tool_unavailable_stops_before_selector_continuation():
    class UnavailableWiki:
        def lookup(self, **kwargs):
            return {'grounding_status': 'llm_unavailable'}
    client = Stage3RecordingClient([json.dumps(native('wiki_lookup', 'id')), json.dumps(FINAL)])
    model = DirectLLMService(client=client, prompt_service=PromptService())
    result = UnifiedTurnService(model=model, wiki_lookup=UnavailableWiki(), calendar_lookup=RecordingCalendar()).run(text='Вопрос', context={})
    assert len(client.calls) == 1
    assert result['final_result']['route'] == 'retry_pending'
    assert len(result['tool_requests']) == 1


@pytest.mark.parametrize("bad_function", [None, {}, {"name": []}, {"name": "unknown", "arguments": "{}"}, {"name": "wiki_lookup", "arguments": "{}"}])
def test_invalid_prior_native_function_fails_closed_before_provider(bad_function):
    first = native("wiki_lookup", "id-0")["_native_tool_calls"][0]
    first["function"] = bad_function
    observation = {"tool": "calendar_lookup", "status": "ready"}
    history = [
        {"role": "assistant", "tool_calls": [first]},
        {"role": "tool", "tool_call_id": "id-0", "content": "{}"},
        {"role": "assistant", "tool_calls": native("calendar_lookup", "id-1")["_native_tool_calls"]},
        {"role": "tool", "tool_call_id": "id-1", "content": json.dumps(observation)},
    ]
    client = Stage3RecordingClient([json.dumps(FINAL)])
    model = DirectLLMService(client=client, prompt_service=PromptService())
    result = model.continue_after_tool(text="Вопрос", context={"native_tool_messages": history}, tool_name="calendar_lookup", tool_call_id="id-1", tool_request=ARGS["calendar_lookup"], observation=observation)
    assert result["kind"] == "final"
    assert result["result"]["route"] == "retry_pending"
    assert result["result"]["reason"] == "tool_call_id_mismatch"
    assert client.calls == []


def test_writer_requires_json_at_system_priority_without_weakening_evidence():
    prompt = DirectLLMService._build_finalization_system_prompt("Нейтральный профиль.", knowledge_mode="kb_grounded")
    assert "Возвращай только JSON-объект по required_json_schema из входного пакета. Клиентский текст помещай только в response_text, не вне JSON." in prompt
    assert "Это единственные источники фактических утверждений для текущего хода, но статус успешного получения и наличие ссылок не доказывают прямого покрытия вопроса." in prompt
    assert "Перефразируй его естественно, не выходя за точный смысл и модальность переданного evidence." in prompt


@pytest.mark.parametrize("order,reason", [
    (("wiki_lookup", "wiki_lookup"), "tool_duplicate_call"),
    (("calendar_lookup", "calendar_lookup"), "tool_duplicate_call"),
    (("wiki_lookup", "calendar_lookup", "wiki_lookup"), "tool_budget_exceeded"),
])
def test_forbidden_sequential_requests_never_execute(order, reason):
    client = Stage3RecordingClient([json.dumps(native(tool, f"id-{i}")) for i, tool in enumerate(order)])
    wiki, calendar = RecordingWiki(), RecordingCalendar()
    model = DirectLLMService(client=client, prompt_service=PromptService())
    result = UnifiedTurnService(model=model, wiki_lookup=wiki, calendar_lookup=calendar).run(text="Вопрос", context={})
    assert result["final_result"]["reason"] == reason
    assert result["final_result"]["route"] == "retry_pending"
    assert result["final_result"]["response_text"] == ""
    assert len(wiki.calls) <= 1 and len(calendar.calls) <= 1
    assert len(result["tool_requests"]) == len(set(order))


def test_mismatched_native_observation_id_never_reaches_provider():
    call = native("wiki_lookup", "id-0")["_native_tool_calls"]
    history = [{"role": "assistant", "tool_calls": call},
               {"role": "tool", "tool_call_id": "different-id", "content": "{}"}]
    client = Stage3RecordingClient([json.dumps(FINAL)])
    model = DirectLLMService(client=client, prompt_service=PromptService())
    result = model.continue_after_tool(text="Вопрос", context={"native_tool_messages": history}, tool_name="wiki_lookup", tool_call_id="id-0", tool_request=ARGS["wiki_lookup"], observation={})
    assert result["result"]["reason"] == "tool_call_id_mismatch"
    assert client.calls == []
