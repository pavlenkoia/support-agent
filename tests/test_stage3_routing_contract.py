"""Stage3 final-writer ownership through real routing and SQLite."""
import json

import pytest

from app.core.db import Base, make_session_factory
from app.schemas.message import InboundMessage
from app.services.direct_llm import DirectLLMService
from app.services.routing import RoutingService
from tests.test_final_response_validation import PromptService


class Client:
    def __init__(self):
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return json.dumps({'route': 'answer', 'response_text': 'Подтверждённый ответ.', 'reason': 'writer', 'confidence': None})


class Loop:
    def __init__(self, result):
        self.result = result

    def run(self, **kwargs):
        return self.result


def invoke(tmp_path, loop_result):
    factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'routing.db'}")
    Base.metadata.create_all(bind=factory.kw['bind'])
    client = Client()
    direct = DirectLLMService(client=client, prompt_service=PromptService())
    routing = RoutingService(session_factory=factory, direct_llm=direct, turn_service=Loop(loop_result), answer_engine_mode='agent_tool_loop')
    result = routing.handle_inbound(InboundMessage(channel='internal_test', external_user_id='synthetic', external_chat_id='synthetic', text='Синтетический вопрос'))
    return result, client


@pytest.mark.parametrize('status', ['llm_unavailable', 'retry_pending', 'unavailable'])
def test_technical_tool_status_cannot_be_overridden_by_finalize(tmp_path, status):
    result, client = invoke(tmp_path, {
        'kb_result': {'grounding_status': status},
        'finalization_requested': {'response_intent': 'answer', 'reason': 'selector'},
        'tool_observations': [{'tool': 'wiki_lookup', 'status': status}],
    })
    assert client.calls == []
    assert result['route']['route'] == 'retry_pending'
    assert result['audit']['response_strategy']['finalizer_invoked'] is False
    assert result['audit']['trace_packet']['finalization_input']['status'] == 'not_invoked'


def test_technical_selector_did_not_invoke_writer(tmp_path):
    result, client = invoke(tmp_path, {'final_result': {'route': 'retry_pending', 'response_text': '', 'reason': 'tool_budget_exceeded', 'confidence': None}})
    assert client.calls == []
    assert result['audit']['response_strategy']['finalizer_invoked'] is False
    assert result['audit']['trace_packet']['finalization_input']['status'] == 'not_invoked'


def test_selector_customer_draft_cannot_bypass_writer(tmp_path):
    result, client = invoke(tmp_path, {'final_result': {'route': 'answer', 'response_text': 'Недопустимый черновик.', 'reason': 'selector', 'confidence': None}})
    assert client.calls == []
    assert result['route']['route'] == 'retry_pending'
    assert result['outcome']['outcome_payload']['response_text'] == ''


def test_native_tool_actions_correlate_to_their_actual_selector_calls():
    from app.services.audit import build_trace_packet, clean_llm_trace

    trace = clean_llm_trace([
        {'entry_kind': 'model_call', 'role': 'direct_llm', 'step': 'customer_turn', 'native_tool_call_id': 'native-wiki'},
        {'entry_kind': 'model_call', 'role': 'kb_agent', 'step': 'grounded_extraction'},
        {'entry_kind': 'model_call', 'role': 'direct_llm', 'step': 'tool_result_selection', 'native_tool_call_id': 'native-calendar'},
        {'entry_kind': 'model_call', 'role': 'direct_llm', 'step': 'tool_result_selection'},
        {'entry_kind': 'model_call', 'role': 'direct_llm', 'step': 'final_response'},
    ])
    packet = build_trace_packet(case={'case_id': 1, 'conversation_id': 1},
        route={'route': 'answer'}, outcome={}, retrieval={'kb_status': 'ready'},
        response_strategy={'agent_actions': ['wiki_lookup', 'calendar_lookup', 'final_response'],
            'finalizer_invoked': True, 'llm_trace': trace,
            'tool_requests': [{'tool': 'wiki_lookup', 'tool_call_id': 'native-wiki'}, {'tool': 'calendar_lookup', 'tool_call_id': 'native-calendar'}]})
    actions = packet['ordered_actions']
    assert [a['trace_local_id'] for a in actions] == ['trace-call-1', 'trace-call-3', 'trace-call-5']
    assert [a['native_tool_call_id'] for a in actions[:2]] == ['native-wiki', 'native-calendar']


def test_social_selection_invokes_common_writer_once(tmp_path):
    result, client = invoke(tmp_path, {'finalization_requested': {'response_intent': 'social_reply', 'reason': 'selector'}})
    assert len(client.calls) == 1
    packet = json.loads(client.calls[0]['user_prompt'])
    assert packet['response_intent'] == 'social_reply'
    assert packet['grounding_evidence']['facts'] == []
    assert packet['tool_facts'] == []
    assert result['audit']['response_strategy']['finalizer_invoked'] is True


@pytest.mark.parametrize("order", [(), ("wiki_lookup",), ("calendar_lookup",), ("wiki_lookup", "calendar_lookup"), ("calendar_lookup", "wiki_lookup")])
def test_all_collection_paths_use_one_real_common_writer(tmp_path, order):
    from app.services.agent_tool_loop import UnifiedTurnService
    from tests.test_stage3_native_protocol import native
    from tests.test_stage3_tool_contract import (
        PartialRecordingWiki,
        RecordingCalendar,
        Stage3RecordingClient,
    )

    intent = "answer" if order else "social_reply"
    responses = [json.dumps(native(tool, f"id-{index}")) for index, tool in enumerate(order)]
    responses += [json.dumps({"action": "finalize", "response_intent": intent, "reason": "ready"}),
                  json.dumps({"route": intent, "response_text": "Подтверждённый ответ.", "reason": "writer", "confidence": None})]
    client = Stage3RecordingClient(responses)
    direct = DirectLLMService(client=client, prompt_service=PromptService())
    # This test covers the two-tool/terminal path; full Wiki coverage is
    # finalized immediately and intentionally skips that selector step.
    wiki, calendar = PartialRecordingWiki(), RecordingCalendar()
    turn = UnifiedTurnService(model=direct, wiki_lookup=wiki, calendar_lookup=calendar)
    factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'paths.db'}")
    Base.metadata.create_all(bind=factory.kw['bind'])
    routing = RoutingService(session_factory=factory, direct_llm=direct, turn_service=turn, answer_engine_mode="agent_tool_loop")
    result = routing.handle_inbound(InboundMessage(channel="internal_test", external_user_id="synthetic", external_chat_id="synthetic", text="Вопрос"))
    # Existing external routing groups social replies under the answer outcome.
    assert result["route"]["route"] == "answer"
    assert result["outcome"]["outcome_payload"]["response_text"] == "Здравствуйте! Подтверждённый ответ."
    assert len(client.calls) == len(order) + 2
    assert len(wiki.calls) == order.count("wiki_lookup")
    assert len(calendar.calls) == order.count("calendar_lookup")
    trace = result["audit"]["trace_packet"]
    assert [a["action"] for a in trace["ordered_actions"]] == [*order, "final_response"]
    assert trace["finalization_input"]["boundary"] == "respond"
    payload = json.loads(client.calls[-1]["user_prompt"])
    assert [fact["text"] for fact in payload["grounding_evidence"]["facts"]] == (["Частичный факт"] if "wiki_lookup" in order else [])
    assert [f["kind"] for f in payload["tool_facts"]] == (["calendar_lookup"] if "calendar_lookup" in order else [])
    assert not {"tool_requests", "llm_trace", "planner_reason", "native_tool_messages"}.intersection(payload)
