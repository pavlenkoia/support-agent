from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.core.db import Base, make_session_factory
from app.models.workflow_event import WorkflowEvent
from app.schemas.message import InboundMessage
from app.services.agent_tool_loop import UnifiedTurnService
from app.services.direct_llm import DirectLLMService
from app.services.probe_service import ProbeService
from app.services.routing import RoutingService
from tests.test_final_response_validation import PromptService


class Model:
    def __init__(self, kind='wiki_lookup', attempts=0, basis='Кандидат'):
        self.kind, self.attempts, self.basis = kind, attempts, basis
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        tools = kwargs.get('tools') or []
        tool_names = {tool.get('function', {}).get('name') for tool in tools if isinstance(tool, dict)}
        if tool_names and not kwargs.get("messages"):
            arguments = ({'query': 'Запрос', 'context_scope': 'Выбранный предмет', 'needed_fact': 'Условие'}
                         if self.kind == 'wiki_lookup' else {'date_expression': '2026-01-01', 'requested_calendar_fact': 'weekday'})
            return json.dumps({'_native_tool_calls': [{'id': 'native-123', 'function': {'name': self.kind, 'arguments': json.dumps(arguments)}}]})
        if tool_names:
            return json.dumps({'action': 'finalize', 'response_intent': 'answer', 'reason': 'need_response'})
        return json.dumps({'route':'answer','response_text':'Подтверждённый ответ.','confidence':None,'reason':'exact-final-reason'})

    def get_last_call_info(self):
        return {'provider':'injected','model':'synthetic','duration_ms':12,'attempts':self.attempts}


class Wiki:
    def __init__(self, basis):
        self.basis = basis

    def lookup(self, **kwargs):
        return {'grounding_status':'ready','answer_basis':self.basis,'grounded_facts':['Факт с условием'],
                'source_refs':['compiled/concepts/synthetic.md'], 'private_raw':'MUST_NOT_BE_CAPTURED'}


class Calendar:
    def lookup(self, **kwargs):
        return {'status':'ready','summary':'Календарный факт','structured':{'iso_date':'2026-01-01'}}


def make_trace_routing(factory, *, kind='wiki_lookup', attempts=0, basis='Кандидат'):
    client = Model(kind, attempts, basis)
    direct = DirectLLMService(client=client, prompt_service=PromptService())
    loop = UnifiedTurnService(model=direct, wiki_lookup=Wiki(basis), calendar_lookup=Calendar())
    return RoutingService(session_factory=factory, direct_llm=direct, turn_service=loop, answer_engine_mode='agent_tool_loop'), client


def run_trace(tmp_path, **kwargs):
    factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path/'audit.db'}")
    Base.metadata.create_all(bind=factory.kw['bind'])
    routing, client = make_trace_routing(factory, **kwargs)
    result = routing.handle_inbound(InboundMessage(channel='internal_test', external_user_id='synthetic',
        external_chat_id='synthetic', external_message_id='message-1', external_event_id='event-1', text='Текущий вопрос'))
    return result, routing, client, factory


@pytest.mark.parametrize('attempts,expected', [(0,0),(None,None),(2,6)])
def test_real_model_metrics_keep_unknown_and_zero(tmp_path, attempts, expected):
    result, _, _, _ = run_trace(tmp_path, attempts=attempts)
    assert result['audit']['route_reason'] == 'exact-final-reason'
    assert result['audit']['route_confidence'] is None
    assert result['audit']['logical_llm_call_count'] == 3
    assert result['audit']['provider_attempt_count'] == expected


@pytest.mark.parametrize('kind', ['wiki_lookup','calendar_lookup'])
def test_packet_is_actual_finalization_input_not_reconstructed_kb(tmp_path, kind):
    result, _, client, _ = run_trace(tmp_path, kind=kind)
    packet = result['audit']['trace_packet']
    assert packet['version'] == 'stage2-c5'
    assert packet['source_turn']['external_event_id'] == 'event-1'
    assert packet['source_turn']['external_message_id'] == 'message-1'
    assert packet['tool_requests'][0]['tool_call_id'] == 'native-123'
    assert packet['tool_requests'][0]['tool_request']
    actual = client.calls[-1]
    captured = packet['finalization_input']
    assert captured['status'] == 'complete'
    sent = json.loads(actual['user_prompt'])
    assert captured['data']['grounding_evidence'] == sent['grounding_evidence']
    assert captured['data']['tool_facts'] == sent['tool_facts']
    assert captured['data']['conversation'] == sent['conversation']
    if kind == 'calendar_lookup':
        assert result['audit']['kb_status'] == 'not_started'
        assert packet['evidence_status'] == 'ready'
    assert 'MUST_NOT_BE_CAPTURED' not in json.dumps(packet)
    assert packet['result']['delivery_status'] == 'not_recorded'
    assert packet['result']['reason'] == 'exact-final-reason'


def test_overflow_is_bounded_explicit_and_does_not_change_answer(tmp_path):
    result, _, _, _ = run_trace(tmp_path, basis='Я'*50000)
    packet = result['audit']['trace_packet']
    assert len(json.dumps(packet,ensure_ascii=False).encode()) <= 16384
    assert packet['finalization_input']['status'] == 'overflow'
    assert packet['finalization_input']['sha256']
    assert packet['result']['reason'] == 'exact-final-reason'
    assert result['route']['route'] == 'answer'
    assert 'Я'*50000 not in json.dumps(result['audit'],ensure_ascii=False)


def test_ordered_actions_link_to_real_calls_with_intermediate_kb_steps(tmp_path):
    from app.services.audit import build_audit_event

    result, _, _, _ = run_trace(tmp_path)
    response_strategy = dict(result['audit']['response_strategy'])
    response_strategy['agent_actions'] = ['navigation', 'coverage_review', 'grounded_extraction', 'final_response']
    response_strategy['llm_trace'] = [
        {'entry_kind': 'model_call', 'step': 'customer_turn', 'provider': 'stub', 'model': 'synthetic', 'duration_ms': 1, 'attempts': 0, 'call_id': 'provider-customer', 'native_tool_call_id': None, 'trace_local_id': 'trace-1', 'input_packet': {'status': 'complete'}},
        {'entry_kind': 'model_call', 'step': 'navigation', 'provider': 'stub', 'model': 'synthetic', 'duration_ms': 2, 'attempts': 0, 'call_id': 'provider-nav', 'native_tool_call_id': 'native-nav', 'trace_local_id': 'trace-2', 'input_packet': {'status': 'complete'}},
        {'entry_kind': 'model_call', 'step': 'coverage_review', 'provider': 'stub', 'model': 'synthetic', 'duration_ms': 3, 'attempts': 0, 'call_id': 'provider-coverage', 'native_tool_call_id': 'native-coverage', 'trace_local_id': 'trace-3', 'input_packet': {'status': 'complete'}},
        {'entry_kind': 'model_call', 'step': 'grounded_extraction', 'provider': 'stub', 'model': 'synthetic', 'duration_ms': 4, 'attempts': 0, 'call_id': 'provider-extract', 'native_tool_call_id': 'native-extract', 'trace_local_id': 'trace-4', 'input_packet': {'status': 'complete'}},
        {'entry_kind': 'model_call', 'step': 'final_response', 'provider': 'stub', 'model': 'synthetic', 'duration_ms': 5, 'attempts': 0, 'call_id': 'provider-final', 'native_tool_call_id': None, 'trace_local_id': 'trace-5', 'input_packet': {'status': 'complete'}},
    ]
    audit = build_audit_event(result['case'], result['route'], result['retrieval'], result['outcome'], {}, response_strategy)
    ordered_actions = audit['trace_packet']['ordered_actions']
    assert [item['action'] for item in ordered_actions] == ['navigation', 'coverage_review', 'grounded_extraction', 'final_response']
    assert [item['trace_local_id'] for item in ordered_actions] == ['trace-2', 'trace-3', 'trace-4', 'trace-5']
    assert [item['provider_call_id'] for item in ordered_actions] == ['provider-nav', 'provider-coverage', 'provider-extract', 'provider-final']
    assert [item['native_tool_call_id'] for item in ordered_actions] == ['native-nav', 'native-coverage', 'native-extract', None]
    assert audit['response_strategy']['agent_actions'] == ['navigation', 'coverage_review', 'grounded_extraction', 'final_response']


def test_ordered_actions_are_structured_and_linked_to_tool_calls(tmp_path):
    result, _, _, _ = run_trace(tmp_path)
    packet = result['audit']['trace_packet']
    ordered_actions = packet['ordered_actions']
    assert ordered_actions[0]['action'] == 'wiki_lookup'
    assert ordered_actions[0]['order'] == 1
    assert ordered_actions[0]['tool_call_id'] == 'native-123'
    assert ordered_actions[0]['model_call']['entry_kind'] == 'model_call'
    assert ordered_actions[-1]['action'] == 'final_response'
    assert ordered_actions[-1]['order'] == len(ordered_actions)
    assert ordered_actions[-1]['tool_call_id'] is None
    assert result['audit']['agent_actions'] == ['wiki_lookup', 'final_response']


def test_delivery_correlates_to_the_same_trace_without_claiming_send_early(tmp_path):
    result, routing, _, factory = run_trace(tmp_path)
    packet = result['audit']['trace_packet']
    routing.record_outbound_message(result['case']['case_id'],result['outcome']['outcome_payload']['response_text'])
    with factory() as session:
        event = session.scalar(select(WorkflowEvent).where(WorkflowEvent.event_type=='answer_delivery_recorded'))
        assert event is not None
        assert event.payload['trace_id'] == packet['trace_id']
        assert event.payload['assistant_message_id']
        assert event.payload['status'] == 'assistant_recorded'


def test_probe_json_roundtrip_contains_full_packet(tmp_path):
    factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path/'probe.db'}")
    Base.metadata.create_all(bind=factory.kw['bind'])
    routing, _ = make_trace_routing(factory)
    probe = ProbeService(routing=routing,session_factory=factory)
    sid = probe.start_session(scenario_name='trace-test')['session_id']
    probe.send_message(sid,'Текущий вопрос')
    trace = probe.get_trace(sid)
    payload = next(x['payload'] for x in trace['events'] if x['event_type']=='inbound_processed')
    assert payload['trace_packet']['finalization_input']['data']['grounding_evidence']['answer_basis'] == 'Кандидат'
    assert any(x['event_type']=='answer_delivery_recorded' for x in trace['events'])


@pytest.mark.parametrize('boundary',['begin_turn','continue_after_tool','respond'])
def test_parse_failure_counts_one_real_call_and_no_metadata(tmp_path,boundary):
    class Bad(Model):
        def generate(self, **kwargs):
            self.calls.append(kwargs)
            return 'not-json'
    client=Bad()
    direct=DirectLLMService(client=client,prompt_service=PromptService())
    if boundary=='begin_turn':
        value=direct.begin_turn(text='x',context={})
    elif boundary=='respond':
        value=direct.respond('x',{})
    else:
        value=direct.continue_after_tool(text='x',context={},tool_name='calendar_lookup',tool_call_id='id',tool_request={'date_expression':'2026-01-01','requested_calendar_fact':'weekday'},observation={})
    calls=[x for x in value['llm_trace'] if x.get('entry_kind')=='model_call']
    assert len(calls)==1
    assert calls[0]['attempts']==0
    assert 'raw' not in json.dumps(value['llm_trace'])



def test_kb_call_without_provider_metadata_is_counted_unknown():
    from app.services.audit import trace_metrics
    from app.services.kb_agent import KBAgentService
    class NoInfo:
        pass
    kb=KBAgentService(client=NoInfo())
    kb._reset_llm_trace()
    kb._record_llm_call("navigation")
    assert trace_metrics(kb._active_llm_trace) == {"logical_llm_call_count":1,"provider_attempt_count":None}


def test_metadata_rows_never_count_as_model_calls():
    from app.services.audit import trace_metrics
    assert trace_metrics([{"step":"tool_result_finalization_result","attempts":3,"route":"answer"}]) == {"logical_llm_call_count":0,"provider_attempt_count":0}



def test_huge_request_cannot_escape_budget_through_legacy_strategy(tmp_path):
    from app.services.audit import build_audit_event, json_bytes
    result, _, _, _ = run_trace(tmp_path)
    strategy=result["audit"]["response_strategy"]
    strategy["tool_requests"]=[{"tool":"wiki_lookup","tool_request":{"query":"Я"*50000}}]
    audit=build_audit_event(result["case"],result["route"],result["retrieval"],result["outcome"],{},strategy)
    assert audit["trace_packet"]["status"] == "overflow"
    assert len(json_bytes(audit["trace_packet"])) <= 16384
    assert audit["trace_packet"]["result"]["text_sha256"]
    assert audit["response_strategy"]["tool_requests"] == []
    assert audit["response_strategy"]["tool_requests_capture"]["status"] == "overflow"
    assert "Я"*50000 not in json.dumps(audit,ensure_ascii=False)


def test_ambiguous_outbound_does_not_guess_trace(tmp_path):
    result, routing, _, factory = run_trace(tmp_path)
    routing.handle_inbound(InboundMessage(channel="internal_test",external_user_id="synthetic",external_chat_id="synthetic",external_message_id="message-2",text="Текущий вопрос"))
    routing.record_outbound_message(result["case"]["case_id"],result["outcome"]["outcome_payload"]["response_text"])
    with factory() as session:
        event=session.scalar(select(WorkflowEvent).where(WorkflowEvent.event_type=="answer_delivery_recorded"))
        assert event.payload["trace_id"] is None
        assert event.payload["correlation_status"] == "ambiguous"


def test_technical_wiki_failure_does_not_invent_finalization(tmp_path):
    factory=make_session_factory(f"sqlite+pysqlite:///{tmp_path/'technical.db'}")
    Base.metadata.create_all(bind=factory.kw['bind'])
    routing,model=make_trace_routing(factory)
    class FailedWiki:
        def lookup(self,**kwargs):
            return {"grounding_status":"retry_pending","reason":"synthetic-failure"}
    routing.turn_service.wiki_lookup=FailedWiki()
    result=routing.handle_inbound(InboundMessage(channel="internal_test",external_user_id="synthetic",external_chat_id="synthetic",text="Текущий вопрос"))
    assert len(model.calls)==1
    assert result["audit"]["trace_packet"]["finalization_input"]["status"]=="not_invoked"
    assert result["audit"]["trace_packet"]["result"]["delivery_status"]=="not_applicable"
    assert result["route"]["reason"]=="tool_unavailable"


@pytest.mark.parametrize("kind,step", [("wiki_lookup","final_response"),("calendar_lookup","final_response")])
def test_real_final_action_references_packet_call_by_local_id(tmp_path,kind,step):
    result,_,_,_=run_trace(tmp_path,kind=kind)
    packet=result["audit"]["trace_packet"]
    indexed={c["trace_local_id"]:c for c in packet["model_calls"]}
    final=packet["ordered_actions"][-1]
    assert indexed[final["trace_local_id"]]["step"]==step
    assert indexed[packet["ordered_actions"][0]["trace_local_id"]]["step"]=="customer_turn"


def test_wiki_actions_ignore_intermediate_kb_call_positions(tmp_path):
    from app.services.audit import build_audit_event
    result,_,_,_=run_trace(tmp_path)
    strategy=dict(result["audit"]["response_strategy"])
    original=strategy["llm_trace"]
    strategy["llm_trace"]=[original[0],*[{"entry_kind":"model_call","role":"kb_agent","step":step,"attempts":1} for step in ["navigation","coverage_review","grounded_extraction"]],*original[1:]]
    packet=build_audit_event(result["case"],result["route"],result["retrieval"],result["outcome"],{},strategy)["trace_packet"]
    assert [x["action"] for x in packet["ordered_actions"]]==["wiki_lookup","final_response"]
    assert packet["ordered_actions"][-1]["model_call"]["step"]=="final_response"
    assert packet["ordered_actions"][-1]["trace_local_id"]==packet["model_calls"][-1]["trace_local_id"]


def test_short_reason_survives_packet_overflow_and_huge_reason_is_receipted(tmp_path):
    from app.services.audit import build_audit_event, json_bytes
    result,_,_,_=run_trace(tmp_path)
    strategy=dict(result["audit"]["response_strategy"])
    strategy["tool_requests"]=[{"tool":"wiki_lookup","tool_request":{"query":"Я"*50000}}]
    audit=build_audit_event(result["case"],result["route"],result["retrieval"],result["outcome"],{},strategy)
    assert audit["trace_packet"]["result"]["reason"]=="exact-final-reason"
    reason="Причина"*10000
    route={**result["route"],"reason":reason,"route_reason":reason}
    audit=build_audit_event(result["case"],route,result["retrieval"],result["outcome"],{},strategy)
    assert audit["route_reason"]==reason
    assert len(json_bytes(audit["trace_packet"]))<=16384
    assert audit["trace_packet"]["result"]["reason"]["status"]=="overflow"


def test_ambiguous_selector_does_not_guess_a_model_call(tmp_path):
    from app.services.audit import build_audit_event
    result,_,_,_=run_trace(tmp_path)
    strategy=dict(result["audit"]["response_strategy"])
    strategy["llm_trace"]=[strategy["llm_trace"][0],*strategy["llm_trace"]]
    packet=build_audit_event(result["case"],result["route"],result["retrieval"],result["outcome"],{},strategy)["trace_packet"]
    assert packet["ordered_actions"][0]["model_call"] is None
    assert packet["ordered_actions"][0]["trace_local_id"] is None
    assert packet["ordered_actions"][0]["tool_call_id"]=="native-123"
