import json
from copy import deepcopy

from app.services.direct_llm import DirectLLMService
from tests.test_final_answer_reliability import Writer, POLICY
from tests.test_stage4_writer_boundary import evidence


def test_cannot_answer_writer_receives_policy_without_incident_details():
    packet = evidence()
    packet['user_question'] = 'PRIVATE_QUESTION_MARKER'
    packet['context_scope'] = 'PRIVATE_SCOPE_MARKER'
    packet['coverage']['status'] = 'none'
    packet['facts'] = []
    packet['coverage']['answered_parts'] = []
    packet['coverage']['missing_parts'] = ['PRIVATE_MISSING_MARKER']
    original = deepcopy(packet)
    writer = Writer('cannot_answer')
    result = DirectLLMService(client=writer).respond(
        'PRIVATE_QUESTION_MARKER',
        {'grounding_status': 'ready', 'source_refs': ['compiled/page.md'], 'answer_evidence': packet},
        tool_observations=[POLICY],
    )
    payload = writer.calls[0]
    assert 'answer' in payload['allowed_routes']
    assert payload['user_message'] == 'PRIVATE_QUESTION_MARKER'
    assert payload['conversation'] == []
    assert payload['grounding_evidence'] == {'facts': []}
    assert payload['tool_facts'] == [POLICY]
    assert 'PRIVATE_SCOPE_MARKER' not in json.dumps(payload)
    assert 'PRIVATE_MISSING_MARKER' not in json.dumps(payload)
    assert packet == original
    assert result['response_text'] == 'Точный модельный текст.'
