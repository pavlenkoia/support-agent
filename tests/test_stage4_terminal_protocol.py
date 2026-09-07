"""Terminal selector wire contract, not semantic model acceptance."""
from copy import deepcopy
import json

import pytest

from app.services.agent_tool_loop import UnifiedTurnService
from app.services.audit import capture_model_input
from app.services.direct_llm import DirectLLMService
from tests.test_stage3_native_protocol import ARGS, FINAL, native
from tests.test_stage3_tool_contract import PromptService, RecordingCalendar, RecordingWiki, Stage3RecordingClient


def terminal_context(order=('wiki_lookup', 'calendar_lookup')):
    history, requests, observations = [], [], []
    for i, name in enumerate(order):
        ident = f'id-{i}'
        observation = {'tool': name, 'status': 'not_found' if name == 'wiki_lookup' else 'ready'}
        requests.append({'tool': name, 'tool_call_id': ident, 'tool_request': deepcopy(ARGS[name])})
        observations.append(observation)
        history.extend([
            {'role': 'assistant', 'tool_calls': native(name, ident)['_native_tool_calls']},
            {'role': 'tool', 'tool_call_id': ident, 'content': json.dumps(observation)},
        ])
    return {'native_tool_messages': history, 'tool_requests': requests, 'tool_observations': observations}


def continue_terminal(context, output=None):
    client = Stage3RecordingClient([json.dumps(FINAL) if output is None else output])
    model = DirectLLMService(client=client, prompt_service=PromptService())
    result = model.continue_after_tool(
        text='Исходный вопрос\nДополнение.', context=context,
        tool_name='calendar_lookup', tool_call_id='id-1', tool_request=ARGS['calendar_lookup'],
        observation={'tool': 'calendar_lookup', 'status': 'ready'},
    )
    return result, client


@pytest.mark.parametrize('order', [('wiki_lookup', 'calendar_lookup'), ('calendar_lookup', 'wiki_lookup')])
def test_terminal_wire_exact_packet_preserves_both_orders_and_native_middle(order):
    client = Stage3RecordingClient([json.dumps(native(name, f'id-{i}')) for i, name in enumerate(order)] + [json.dumps(FINAL)])
    wiki, calendar = RecordingWiki(), RecordingCalendar()
    result = UnifiedTurnService(model=DirectLLMService(client=client, prompt_service=PromptService()), wiki_lookup=wiki, calendar_lookup=calendar).run(text='Исходный вопрос\nДополнение.', context={})
    assert result['finalization_requested'] == {'response_intent': 'answer', 'reason': 'done'}
    assert len(client.calls) == 3
    assert len(wiki.calls) == len(calendar.calls) == 1
    middle = client.calls[1]['messages']
    assert [m['tool_call_id'] for m in middle if m['role'] == 'tool'] == ['id-0']
    assert [m['tool_calls'][0]['id'] for m in middle if m['role'] == 'assistant'] == ['id-0']
    wire = client.calls[2]
    assert [m['role'] for m in wire['messages']] == ['system', 'user']
    assert not any(k in wire for k in ('tools', 'tool_choice', 'parallel_tool_calls'))
    assert wire['response_format'] == {'type': 'json_object'}
    packet = json.loads(wire['messages'][1]['content'])
    assert set(packet) == {'protocol_version','selector_phase','task','required_json_schema','user_message','first_reply_in_dialogue','conversation','rules','tool_state','remaining_tool_calls','tool_exchanges'}
    assert packet['protocol_version'] == 'selector-terminal/v1'
    assert packet['selector_phase'] == 'terminal'
    assert packet['user_message'] == 'Исходный вопрос\nДополнение.'
    assert packet['remaining_tool_calls'] == 0
    assert packet['rules'] == json.loads(client.calls[0]['user_prompt'])['rules']
    assert packet['tool_exchanges'] == [
        {'tool_call_id': r['tool_call_id'], 'name': r['tool'], 'arguments': r['tool_request'], 'observation': obs}
        for r, obs in zip(result['tool_requests'], result['tool_observations'])
    ]
    assert packet['tool_state'] == {o['tool']: o['status'] for o in result['tool_observations']}
    recorded = result['llm_trace'][-1]['input_packet']
    assert recorded['data']['tool_exchanges'] == packet['tool_exchanges']
    assert recorded['data']['protocol_version'] == 'selector-terminal/v1'
    assert 'Сейчас выполняется терминальный выбор: бюджет инструментов исчерпан.' in wire['messages'][0]['content']


@pytest.mark.parametrize('output', [
    '<tool_call><function=finalize></function></tool_call>',
    '```json\n'+json.dumps(FINAL)+'\n```',
    json.dumps(FINAL)+' trailing',
    json.dumps(FINAL)+' {}',
    json.dumps({**FINAL, 'response_text': 'draft'}),
    json.dumps({**FINAL, 'response_intent': 'unsupported'}),
    json.dumps({**FINAL, 'action': 'other'}),
    'null', '[]', '{"action":"finalize","response_intent":"answer","reason":NaN}',
])
def test_terminal_invalid_output_is_textless_and_never_synthesized(output):
    result, client = continue_terminal(terminal_context(), output)
    assert result['kind'] == 'final'
    assert result['result']['route'] == 'retry_pending'
    assert result['result']['response_text'] == ''
    assert result['result']['reason'] in {'tool_result_finalization_failed', 'invalid_selector_output'}
    assert len(client.calls) == 1


@pytest.mark.parametrize('location', ['first', 'last'])
@pytest.mark.parametrize('damage', ['id','type','name','arguments','result','role','missing-call','non-object'])
def test_both_native_pairs_fail_closed_before_projection_or_provider(location, damage):
    context = terminal_context()
    i = 0 if location == 'first' else 2
    a, t = context['native_tool_messages'][i:i+2]
    if damage == 'id':
        t['tool_call_id'] = 'wrong'
    elif damage == 'type':
        a['tool_calls'][0]['type'] = 'other'
    elif damage == 'name':
        a['tool_calls'][0]['function']['name'] = 'unknown'
    elif damage == 'arguments':
        a['tool_calls'][0]['function']['arguments'] = '{'
    elif damage == 'result':
        t['content'] = json.dumps({'tool': 'changed', 'status': 'ready'})
    elif damage == 'role':
        t['role'] = 'user'
    elif damage == 'missing-call':
        a['tool_calls'] = []
    else:
        context['native_tool_messages'][i+1] = None
    result, client = continue_terminal(context)
    assert result['result']['reason'] == 'tool_call_id_mismatch'
    assert client.calls == []


@pytest.mark.parametrize('key', ['tool_requests','tool_observations'])
@pytest.mark.parametrize('damage', ['missing','reordered','changed','empty'])
def test_terminal_requires_all_execution_records_to_match_history(key, damage):
    context = terminal_context()
    if damage == 'missing':
        context.pop(key)
    elif damage == 'reordered':
        context[key].reverse()
    elif damage == 'empty':
        context[key] = []
    elif key == 'tool_requests':
        context[key][0]['tool_request']['query'] = 'different'
    else:
        context[key][0] = {'tool': 'wiki_lookup','status':'ready'}
    result, client = continue_terminal(context)
    assert result['result']['reason'] == 'tool_call_id_mismatch'
    assert client.calls == []


def test_terminal_valid_finalize_is_model_output_not_application_default():
    result, _ = continue_terminal(terminal_context(), json.dumps({'action':'finalize','response_intent':'missing_grounding','reason':'model-chosen'}))
    assert result['kind'] == 'finalization_requested'
    assert result['response_intent'] == 'missing_grounding'
    assert result['reason'] == 'model-chosen'


def test_no_history_single_tool_compatibility_keeps_native_pair():
    client = Stage3RecordingClient([json.dumps(FINAL)])
    result = DirectLLMService(client=client, prompt_service=PromptService()).continue_after_tool(
        text='Вопрос', context={}, tool_name='wiki_lookup', tool_call_id='original-id',
        tool_request=ARGS['wiki_lookup'], observation={'tool':'wiki_lookup','status':'not_found'},
    )
    assert result['kind'] == 'finalization_requested'
    assert [m['tool_call_id'] for m in client.calls[0]['messages'] if m['role']=='tool'] == ['original-id']
    assert [t['function']['name'] for t in client.calls[0]['tools']] == ['calendar_lookup']


def test_capture_model_input_exact_terminal_allowlist_not_instructions():
    factual = {'protocol_version':'selector-terminal/v1','selector_phase':'terminal','tool_exchanges':[], 'tool_state':{},'remaining_tool_calls':0}
    packet = capture_model_input('continue_after_tool', {**factual,'task':'private instruction','reason':'private rationale','tool_request':{}})
    assert packet['data'] == factual


def test_selector_subject_contract_is_in_primary_system_role_and_task():
    model = DirectLLMService(client=Stage3RecordingClient([]), prompt_service=PromptService())
    system = model._build_selector_system_prompt('Нейтральный профиль.')
    assert 'Точность передачи вопроса в инструмент важнее удобства его переформулирования.' in system
    task = json.loads(model._build_selector_prompt(text='Вопрос', context={}))['task']
    assert task.startswith('Выбери следующее действие для исходного вопроса user_message с учётом явно выбранного клиентом предмета в conversation.')


@pytest.mark.parametrize('order', [('wiki_lookup', 'calendar_lookup'), ('calendar_lookup', 'wiki_lookup')])
def test_real_compatible_http_payload_preserves_terminal_protocol_without_network(monkeypatch, order):
    from app.integrations.llm.openai_compatible import OpenAICompatibleClient
    from tests.test_openai_compatible_client import FakeResponse

    payloads = []
    def fake_urlopen(req, timeout):
        payloads.append(json.loads(req.data))
        index = len(payloads) - 1
        if index < 2:
            message = {'content': None, 'tool_calls': native(order[index], f'id-{index}')['_native_tool_calls']}
        else:
            message = {'content': json.dumps(FINAL)}
        return FakeResponse({'choices': [{'message': message}]})
    monkeypatch.setattr('app.integrations.llm.openai_compatible.request.urlopen', fake_urlopen)
    client = OpenAICompatibleClient(provider='openai_compatible', base_url='https://terminal-protocol.invalid/v1', api_key='synthetic-test-only', model='synthetic', max_retries=0)
    result = UnifiedTurnService(model=DirectLLMService(client=client, prompt_service=PromptService()), wiki_lookup=RecordingWiki(), calendar_lookup=RecordingCalendar()).run(text='Исходный вопрос', context={})
    assert result['finalization_requested']['response_intent'] == 'answer'
    assert len(payloads) == 3
    assert [m['role'] for m in payloads[-1]['messages']] == ['system', 'user']
    assert not any(k in payloads[-1] for k in ('tools','tool_choice','parallel_tool_calls'))
    assert payloads[-1]['response_format'] == {'type':'json_object'}
    assert [m['tool_call_id'] for m in payloads[1]['messages'] if m['role']=='tool'] == ['id-0']
    assert [x['name'] for x in json.loads(payloads[-1]['messages'][1]['content'])['tool_exchanges']] == list(order)


def test_terminal_xml_has_exact_parse_reason():
    result, client = continue_terminal(terminal_context(), '<tool_call><function=finalize/></tool_call>')
    assert result['result']['reason'] == 'tool_result_finalization_failed'
    assert result['result']['response_text'] == ''
    assert len(client.calls) == 1
