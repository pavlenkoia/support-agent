"""Final reliability contract: model text is never replaced by a synthetic refusal."""
import json
from copy import deepcopy

import pytest

from app.services.answer_evidence import EvidenceValidationError, validate_answer_evidence
from app.services.direct_llm import DirectLLMService
from tests.test_stage4_writer_boundary import evidence
from tests.test_openai_compatible_client import FakeResponse
from app.integrations.llm.openai_compatible import OpenAICompatibleClient


POLICY = {'kind': 'profile_no_answer_option', 'summary': 'Для уточнения можно обратиться в офис.', 'source_ref': 'profile:policy'}
CALENDAR = {'tool': 'calendar_lookup', 'status': 'ready', 'summary': '7 сентября — понедельник.', 'structured': {'iso_date': '2026-09-07', 'weekday_ru': 'понедельник', 'is_weekend': False, 'year': 2026}}


class Writer:
    def __init__(self, route):
        self.route = route
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(json.loads(kwargs['user_prompt']))
        return json.dumps({'route': self.route, 'response_text': 'Точный модельный текст.', 'confidence': None, 'reason': 'model'})

    def get_last_call_info(self):
        return {}


@pytest.mark.parametrize('status', ['none', 'conflicting'])
@pytest.mark.parametrize('calendar', [False, True])
@pytest.mark.parametrize('route', ['cannot_answer', 'answer'])
def test_non_answerable_writer_restriction(status, calendar, route):
    packet = evidence()
    packet['coverage']['status'] = status
    packet['facts'] = []
    packet['coverage']['answered_parts'] = []
    original = deepcopy(packet)
    writer = Writer(route)
    result = DirectLLMService(client=writer).respond('Первое и второе?', {'grounding_status': 'ready', 'source_refs': ['compiled/page.md'], 'answer_evidence': packet}, tool_observations=[POLICY] + ([CALENDAR] if calendar else []))
    assert len(writer.calls) == 1
    payload = writer.calls[0]
    assert payload['allowed_routes'] == ['cannot_answer']
    # The writer gets only profile policy; factual packet and dialogue stay in audit.
    assert payload['grounding_evidence'] == {'policy_evidence': [POLICY]}
    assert payload['tool_facts'] == []
    assert 'Первое возможно при условии.' not in json.dumps(payload, ensure_ascii=False)
    assert 'Кандидатная сводка' not in json.dumps(payload, ensure_ascii=False)
    assert packet == original
    if route == 'cannot_answer':
        assert result['route'] == route
        assert result['response_text'] == 'Точный модельный текст.'
    else:
        assert result['route'] == 'retry_pending'
        assert result['response_text'] == ''
        assert result['reason'] == 'final_response_route_not_allowed'


def test_writer_v2_primary_task_preserves_uncertainty_and_context():
    writer = Writer('cannot_answer')
    packet = evidence()
    packet['coverage']['status'] = 'none'
    packet['facts'] = []
    packet['coverage']['answered_parts'] = []
    DirectLLMService(client=writer).respond(
        'Вопрос', {'grounding_status': 'ready', 'source_refs': ['compiled/page.md'], 'answer_evidence': packet},
        tool_observations=[POLICY],
    )
    payload = writer.calls[0]
    assert 'клиентский текст содержит только естественно сформулированный профильный fallback' in payload['task']
    assert 'Не добавляй объяснение отсутствия сведений' in payload['task']
    assert 'Служебные основания оставь только в reason' in payload['task']
    assert 'Сформулируй готовый естественный ответ на русском языке.' in payload['output_rules']
    assert 'Не склеивай извлечённые факты механически.' in payload['output_rules']
    assert any('весь response_text — только профильный fallback' in r for r in payload['output_rules'])
    assert payload['grounding_evidence']['policy_evidence'] == [POLICY]
    assert payload['allowed_routes'] == ['cannot_answer']
    system = DirectLLMService._build_finalization_system_prompt('', knowledge_mode='kb_grounded')
    assert 'При route=cannot_answer весь response_text содержит только' in system
    assert 'при cannot_answer они не применяются' in system


@pytest.mark.parametrize('bad', ['missing_parts', 'unresolved_constraints', 'conflicts', 'not_ready', 'no_facts'])
def test_inconsistent_full_rejected(bad):
    packet = evidence()
    packet['coverage'].update(status='full', missing_parts=[])
    if bad == 'not_ready':
        packet['acquisition_status'] = 'not_found'
    elif bad == 'no_facts':
        packet['facts'] = []
        packet['coverage']['answered_parts'] = []
    elif bad == 'conflicts':
        packet['coverage'][bad] = [{'fact_ids': ['f1'], 'description': 'conflict'}]
    else:
        packet['coverage'][bad] = ['unknown']
    with pytest.raises(EvidenceValidationError, match='evidence_coverage_inconsistent'):
        validate_answer_evidence(packet)


@pytest.mark.parametrize('kind', ['full', 'calendar', 'social'])
def test_successful_answer_not_replaced(kind):
    writer = Writer('social_reply' if kind == 'social' else 'answer')
    packet = evidence()
    packet['coverage'].update(status='full', missing_parts=[])
    result = DirectLLMService(client=writer).respond('Вопрос', {'grounding_status': 'ready', 'source_refs': ['compiled/page.md'], 'answer_evidence': packet} if kind == 'full' else {}, tool_observations=[CALENDAR] if kind == 'calendar' else [], response_intent='social_reply' if kind == 'social' else 'answer')
    assert result['route'] == writer.route
    assert result['response_text'] == 'Точный модельный текст.'


@pytest.mark.parametrize('remaining,expected', [(300, 120), (60, 60), (0, None)])
def test_http_attempt_budget_not_divided(monkeypatch, remaining, expected):
    clock = iter([0.0])
    monkeypatch.setattr('app.integrations.llm.openai_compatible.time.perf_counter', lambda: next(clock, 300.0 - remaining))
    calls = []
    def transport(req, timeout):
        calls.append(timeout)
        return FakeResponse({'choices': [{'message': {'content': 'ok'}}]})
    monkeypatch.setattr('app.integrations.llm.openai_compatible.request.urlopen', transport)
    client = OpenAICompatibleClient(provider='openai_compatible', base_url='http://example.test/v1', api_key='test', model='test', timeout_seconds=120, max_retries=3, retry_deadline_seconds=300)
    if expected is None:
        with pytest.raises(Exception, match='deadline'):
            client.generate(system_prompt='s', user_prompt='u')
        assert calls == []
        assert client.get_last_call_info()['attempts'] == 0
    else:
        assert client.generate(system_prompt='s', user_prompt='u') == 'ok'
        assert calls == [expected]
        assert client.get_last_call_info()['attempts'] == 1


@pytest.mark.parametrize('channel', ['telegram', 'vk'])
@pytest.mark.parametrize('route', ['cannot_answer', 'answer'])
def test_actual_channel_boundary_no_synthetic_refusal(tmp_path, monkeypatch, channel, route):
    from tests.test_final_response_validation import ReadyWikiTurn, run_delivery, reply
    packet = evidence()
    def turn(self, **kwargs):
        return {'finalization_requested': {'response_intent': 'answer', 'reason': 'collected'},
                'kb_result': {'grounding_status': 'ready', 'source_refs': ['compiled/page.md'], 'answer_evidence': packet},
                'tool_observations': [{'tool': 'wiki_lookup', 'status': 'ready', 'answer_evidence': packet}],
                'trace': {'actions': ['wiki_lookup']}, 'llm_trace': []}
    monkeypatch.setattr(ReadyWikiTurn, 'run', turn)
    result, sent, persisted, _sends, _statuses = run_delivery(tmp_path, channel, reply(route=route, response_text='Точный модельный текст.'), wiki=True)
    if route == 'answer':
        assert result['route']['route'] == 'answer'
        assert sent == persisted == ['Здравствуйте! Точный модельный текст.']
    else:
        assert result['route']['route'] == 'cannot_answer'
        assert sent == persisted == ['Здравствуйте! Точный модельный текст.']


@pytest.mark.parametrize('status,intent,routes', [
    ('ambiguous', 'clarification', ['clarification_requested', 'cannot_answer']),
    ('partial', 'clarification', ['cannot_answer']),
    ('none', 'answer', ['cannot_answer']),
])
def test_coverage_restriction_does_not_use_intent_as_evidence(status, intent, routes):
    from app.services.answer_evidence import allowed_answer_routes
    packet = evidence()
    packet['coverage'].update(status=status, missing_parts=[])
    assert allowed_answer_routes(packet, wiki_executed=True, calendar_executed=False, response_intent=intent) == routes


def test_missing_wiki_with_calendar_cannot_bypass_restriction():
    writer = Writer('cannot_answer')
    result = DirectLLMService(client=writer).respond('Вопрос', {}, tool_observations=[{'tool': 'wiki_lookup', 'status': 'not_found'}, CALENDAR, POLICY])
    assert writer.calls[0]['allowed_routes'] == ['cannot_answer']
    assert result['route'] == 'cannot_answer'


def test_http_diagnostic_preserves_original_error_and_actual_timeout(monkeypatch):
    from app.integrations.llm.openai_compatible import LLMRecoveryExhausted
    clock = {'now': 0.0}
    monkeypatch.setattr('app.integrations.llm.openai_compatible.time.perf_counter', lambda: clock['now'])
    def transport(req, timeout):
        clock['now'] += timeout
        raise TimeoutError('private error must not enter diagnostic')
    monkeypatch.setattr('app.integrations.llm.openai_compatible.request.urlopen', transport)
    client = OpenAICompatibleClient(provider='openai_compatible', base_url='http://example.test/v1', api_key='test', model='test', timeout_seconds=120, max_retries=3, retry_deadline_seconds=120)
    with pytest.raises(LLMRecoveryExhausted):
        client.generate(system_prompt='s', user_prompt='u')
    assert client.get_last_call_info()['attempt_diagnostics'] == [{'attempt': 1, 'timeout_seconds': 120, 'error_type': 'TimeoutError'}]
    assert client.get_last_call_info()['attempts'] == 1


def test_outer_nonready_cannot_promote_inner_full():
    packet = evidence()
    packet['coverage'].update(status='full', missing_parts=[])
    writer = Writer('answer')
    result = DirectLLMService(client=writer).respond('Вопрос', {'grounding_status': 'not_found', 'answer_evidence': packet, 'source_refs': ['compiled/page.md']})
    assert result['route'] == 'retry_pending'
    assert result['response_text'] == ''
    assert result['reason'] == 'evidence_acquisition_mismatch'
    assert writer.calls == []


def test_social_forbidden_answer_is_not_relabelled():
    writer = Writer('answer')
    result = DirectLLMService(client=writer).respond('Спасибо!', {}, response_intent='social_reply')
    assert result['route'] == 'retry_pending'
    assert result['response_text'] == ''
    assert result['reason'] == 'final_response_route_not_allowed'
