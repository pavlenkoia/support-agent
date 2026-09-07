import json
from copy import deepcopy

from app.services.direct_llm import DirectLLMService
from tests.test_final_answer_reliability import Writer, POLICY
from tests.test_stage4_writer_boundary import evidence


def test_cannot_answer_writer_receives_policy_without_incident_details():
    packet = evidence()
    packet['user_question'] = 'PRIVATE_QUESTION_MARKER'
    packet['context_scope'] = 'PRIVATE_SCOPE_MARKER'
    packet['coverage']['status'] = 'partial'
    packet['coverage']['missing_parts'] = ['PRIVATE_MISSING_MARKER']
    original = deepcopy(packet)
    writer = Writer('cannot_answer')
    result = DirectLLMService(client=writer).respond(
        'PRIVATE_QUESTION_MARKER',
        {'grounding_status': 'ready', 'source_refs': ['compiled/page.md'], 'answer_evidence': packet},
        tool_observations=[POLICY],
    )
    payload = writer.calls[0]
    assert payload['allowed_routes'] == ['cannot_answer']
    assert 'user_message' not in payload
    assert 'conversation' not in payload
    assert payload['grounding_evidence'] == {'policy_evidence': [POLICY]}
    assert payload['tool_facts'] == []
    assert 'PRIVATE_' not in json.dumps(payload)
    assert packet == original
    assert result['response_text'] == 'Точный модельный текст.'
