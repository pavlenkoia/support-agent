import json

from app.services.direct_llm import DirectLLMService


class Client:
    def __init__(self):
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(json.loads(kwargs['user_prompt']))
        return json.dumps({'route': 'answer', 'response_text': 'Подтверждённая часть.', 'confidence': None, 'reason': 'evidence'})

    def get_last_call_info(self):
        return {'provider': 'synthetic', 'model': 'synthetic'}


def evidence():
    return {
        'schema_version': 'answer-evidence/v1', 'user_question': 'Первое и второе?', 'context_scope': 'Выбранный предмет',
        'acquisition_status': 'ready', 'answer_basis': 'Кандидатная сводка',
        'facts': [{'id': 'f1', 'text': 'Первое возможно при условии.', 'source_refs': ['compiled/page.md'],
                   'conditions': ['при условии'], 'modality': 'возможно'}],
        'coverage': {'status': 'partial', 'answered_parts': [{'question_part': 'Первое?', 'fact_ids': ['f1']}],
                     'missing_parts': ['Второе?'], 'conflicts': [], 'unresolved_constraints': []},
        'calendar_facts': [], 'policy_evidence': [],
    }


def test_writer_receives_typed_full_evidence_without_losing_conditions():
    client = Client()
    packet = evidence()
    packet['coverage'].update(status='full', missing_parts=[])
    result = DirectLLMService(client=client).respond('Первое и второе?', {
        'grounding_status': 'ready', 'answer_evidence': packet, 'source_refs': ['compiled/page.md'],
        'answer_basis': 'Не доверять дублирующим legacy-полям', 'grounded_facts': ['Не доверять'],
    })
    assert result['route'] == 'answer'
    assert len(client.calls) == 1
    actual = client.calls[0]['grounding_evidence']
    assert actual == packet


def test_partial_evidence_with_covered_facts_reaches_writer_without_gap_diagnostics():
    client = Client()
    packet = evidence()

    result = DirectLLMService(client=client).respond(
        'Расскажите об услуге.',
        {'grounding_status': 'ready', 'answer_evidence': packet, 'source_refs': ['compiled/page.md']},
    )

    assert result['route'] == 'answer'
    assert len(client.calls) == 1
    payload = client.calls[0]
    assert 'answer' in payload['allowed_routes']
    assert payload['grounding_evidence'] == {
        'schema_version': 'answer-evidence/v1',
        'user_question': 'Первое и второе?',
        'context_scope': 'Выбранный предмет',
        'acquisition_status': 'ready',
        'facts': [{
            'id': 'f1',
            'text': 'Первое возможно при условии.',
            'source_refs': ['compiled/page.md'],
            'conditions': ['при условии'],
            'modality': 'возможно',
        }],
        'coverage': {
            'status': 'full',
            'answered_parts': [{'question_part': 'Первое?', 'fact_ids': ['f1']}],
            'missing_parts': [],
            'conflicts': [],
            'unresolved_constraints': [],
        },
        'answer_basis': '',
        'calendar_facts': [],
        'policy_evidence': [],
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    assert 'Второе?' not in serialized
    assert 'Кандидатная сводка' not in serialized


def test_malformed_ready_packet_does_not_invoke_writer():
    client = Client()
    result = DirectLLMService(client=client).respond('Вопрос?', {'grounding_status': 'ready', 'answer_evidence': {'facts': [{}]}})
    assert result['route'] == 'retry_pending'
    assert result['response_text'] == ''
    assert not client.calls


def test_calendar_and_policy_sources_are_kept_separate_and_unknown_tools_omitted():
    projected = DirectLLMService._build_finalization_tool_facts([
        {'tool': 'calendar_lookup', 'status': 'ready', 'summary': 'Дата приходится на понедельник.',
         'structured': {'iso_date': '2026-09-07', 'weekday_ru': 'понедельник', 'is_weekend': False, 'year': 2026}},
        {'kind': 'profile_no_answer_option', 'summary': 'Разрешённый следующий шаг.', 'source_ref': 'profile:policy'},
        {'kind': 'unknown', 'summary': 'Не является evidence'},
    ])
    assert len(projected) == 2
    assert projected[0]['structured']['iso_date'] == '2026-09-07'
    assert projected[1]['source_ref'] == 'profile:policy'


def test_wiki_tool_carries_literal_question_separately_from_search_query():
    from app.services.agent_tool_loop import WikiLookupTool

    class Retrieval:
        def retrieve(self, *args, **kwargs):
            return {"kb_architecture": "llm_wiki", "kb_mode": "llm_wiki_catalog", "kb_snippets": []}

    class KB:
        def read(self, query, pages, **kwargs):
            assert query == "поисковая формулировка"
            assert kwargs["conversation_context"]["user_question"] == "Первый фрагмент. Второй фрагмент."
            return {"grounding_status": "not_found"}

    WikiLookupTool(retrieval=Retrieval(), kb_agent=KB(), knowledge_backend="llm_wiki", knowledge_root="/").lookup(
        text="Первый фрагмент. Второй фрагмент.", context={},
        tool_request={"query": "поисковая формулировка", "needed_fact": "полный вопрос", "context_scope": "предмет"},
    )


def test_loop_keeps_specific_evidence_failure_reason_and_never_continues():
    from app.services.agent_tool_loop import UnifiedTurnService

    class Model:
        def begin_turn(self, **kwargs):
            return {"kind": "wiki_lookup", "tool_call_id": "native-1", "tool_request": {
                "query": "вопрос", "needed_fact": "факт", "context_scope": "предмет"}}
        def continue_after_tool(self, **kwargs):
            raise AssertionError("technical failure must not continue")

    class Wiki:
        def lookup(self, **kwargs):
            return {"grounding_status": "retry_pending", "reason": "evidence_packet_too_large"}

    result = UnifiedTurnService(model=Model(), wiki_lookup=Wiki(), calendar_lookup=None).run(text="вопрос", context={})
    assert result["final_result"]["reason"] == "evidence_packet_too_large"
    assert result["final_result"]["response_text"] == ""


def test_loop_preserves_coverage_for_model_continuation():
    from app.services.agent_tool_loop import UnifiedTurnService

    packet = evidence()
    class Model:
        def begin_turn(self, **kwargs):
            return {"kind": "wiki_lookup", "tool_call_id": "native-1", "tool_request": {
                "query": "вопрос", "needed_fact": "факт", "context_scope": "предмет"}}
        def continue_after_tool(self, **kwargs):
            assert kwargs["observation"]["answer_evidence"] == packet
            return {"kind": "finalization_requested", "response_intent": "answer", "reason": "collected"}
    class Wiki:
        def lookup(self, **kwargs):
            return {"grounding_status": "ready", "source_refs": ["compiled/page.md"], "answer_evidence": packet}
    result = UnifiedTurnService(model=Model(), wiki_lookup=Wiki(), calendar_lookup=None).run(text="вопрос", context={})
    assert result["finalization_requested"]["response_intent"] == "answer"


import pytest


@pytest.mark.parametrize("field,bad", [("acquisition_status", []), ("acquisition_status", {}), ("coverage", {"status": []}),
    ("calendar_facts", [{"kind": "calendar_lookup", "summary": {}, "source_ref": "tool:calendar_lookup", "structured": {}}]),
    ("calendar_facts", [{"kind": "unknown", "summary": "text", "source_ref": "tool:unknown", "structured": {}}]),
    ("policy_evidence", [{"kind": "profile_no_answer_option", "summary": [], "source_ref": "profile:policy"}])])
def test_invalid_typed_fields_fail_closed_instead_of_throwing(field, bad):
    from app.services.answer_evidence import EvidenceValidationError, validate_answer_evidence
    packet = evidence()
    packet[field] = bad
    with pytest.raises(EvidenceValidationError):
        validate_answer_evidence(packet)


@pytest.mark.parametrize("status", ["partial", "none", "ambiguous", "conflicting"])
def test_semantic_labels_are_preserved_not_recomputed_by_application(status):
    from app.services.answer_evidence import validate_answer_evidence
    packet = evidence()
    packet["coverage"]["status"] = status
    packet["coverage"]["conflicts"] = [{"fact_ids": ["f1"], "description": "Неразрешённое противоречие"}]
    packet["coverage"]["unresolved_constraints"] = ["Выбранный предмет"]
    result = validate_answer_evidence(packet)
    assert result == packet
    assert result is not packet
