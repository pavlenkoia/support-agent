import pytest

from app.services.direct_llm import DirectLLMService
from tests.test_stage4_writer_boundary import evidence
from tests.test_final_answer_reliability import Writer


OFFICE_POLICY = {
    'kind': 'profile_no_answer_option',
    'summary': 'Для уточнения вопроса можно позвонить в офис в рабочее время.',
    'source_ref': 'kb/entities/office-chelyabinsk.md',
}


@pytest.mark.parametrize('status', ['partial', 'none', 'conflicting'])
def test_fixed_office_fallback_does_not_call_model(status):
    packet = evidence()
    packet['coverage']['status'] = status
    writer = Writer('answer')
    result = DirectLLMService(client=writer).respond(
        'Вопрос без прямого ответа',
        {'grounding_status': 'ready', 'source_refs': ['compiled/page.md'], 'answer_evidence': packet},
        tool_observations=[OFFICE_POLICY],
    )
    assert writer.calls == []
    assert result['route'] == 'cannot_answer'
    assert result['response_text'] == 'Пожалуйста, позвоните в офис в рабочее время.'
    assert result['reason'] == 'fixed_profile_fallback'
    assert result['llm_trace'] == []


def test_known_answer_keeps_model_generation():
    packet = evidence()
    packet['coverage'].update(status='full', missing_parts=[])
    writer = Writer('answer')
    result = DirectLLMService(client=writer).respond(
        'Прямой вопрос',
        {'grounding_status': 'ready', 'source_refs': ['compiled/page.md'], 'answer_evidence': packet},
        tool_observations=[OFFICE_POLICY],
    )
    assert len(writer.calls) == 1
    assert result['route'] == 'answer'
    assert result['response_text'] == 'Точный модельный текст.'


def test_technical_failure_does_not_become_office_fallback():
    packet = evidence()
    packet['acquisition_status'] = 'unavailable'
    writer = Writer('cannot_answer')
    result = DirectLLMService(client=writer).respond(
        'Вопрос',
        {'grounding_status': 'unavailable', 'answer_evidence': packet},
        tool_observations=[OFFICE_POLICY],
    )
    assert writer.calls == []
    assert result['route'] == 'retry_pending'
    assert result['response_text'] == ''


@pytest.mark.parametrize('channel', ['telegram', 'vk'])
def test_channel_routing_keeps_fixed_fallback_literal(monkeypatch, channel):
    from types import SimpleNamespace
    from app.services.routing import RoutingService
    from tests.test_stage4_routing_policy_evidence import FakeSession, FakeTurnService

    writer = Writer('answer')
    direct = DirectLLMService(client=writer)
    def forbidden_formatter(*args, **kwargs):
        raise AssertionError('Fixed fallback must not be rewritten')
    policy = SimpleNamespace(
        no_answer_policy_evidence=lambda: [{'text': OFFICE_POLICY['summary'], 'source_ref': OFFICE_POLICY['source_ref']}],
        finalize_simple_customer_text=forbidden_formatter,
    )
    turn = FakeTurnService(finalization_requested={'response_intent': 'missing_grounding'})
    service = RoutingService(session_factory=lambda: FakeSession(), direct_llm=direct,
                             policy=policy, turn_service=turn, answer_engine_mode='agent_tool_loop')
    monkeypatch.setattr('app.services.routing.build_context', lambda *args, **kwargs: {'recent_messages': [], 'conversation_id': 1, 'case_state': {'case_status': 'open'}})
    monkeypatch.setattr('app.services.routing.build_audit_event', lambda *args, **kwargs: {'response_strategy': {'answer_engine': 'agent_tool_loop'}})
    monkeypatch.setattr('app.services.routing.persist_workflow_event', lambda *args, **kwargs: None)
    monkeypatch.setattr('app.services.routing.persist_outbound_message', lambda *args, **kwargs: None)
    result = service._handle_agent_tool_loop_inbound(
        FakeSession(), {'case_id': 1, 'case_status': 'open'},
        SimpleNamespace(text='Вопрос', channel=channel, external_message_id='m1', external_event_id='e1'),
    )
    assert writer.calls == []
    assert result['outcome']['outcome_payload']['response_text'] == 'Пожалуйста, позвоните в офис в рабочее время.'
