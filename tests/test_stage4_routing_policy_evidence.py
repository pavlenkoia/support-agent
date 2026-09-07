from __future__ import annotations

from types import SimpleNamespace

from app.services.routing import RoutingService


class FakeSession:
    def __init__(self):
        self.committed = False
        self.support_case = SimpleNamespace(status="open", route_mode=None)

    def scalar(self, _query):
        return self.support_case

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeDirectLLM:
    def __init__(self):
        self.calls = []

    def respond(self, *args, **kwargs):
        self.calls.append(kwargs)
        return {"route": "answer", "response_text": "ok", "confidence": 1.0, "reason": "done"}


class FakeTurnService:
    def __init__(self, *, finalization_requested, final_result=None):
        self.finalization_requested = finalization_requested
        self.final_result = final_result
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        result = {
            "finalization_requested": self.finalization_requested,
            "tool_observations": [],
            "tool_requests": [],
            "llm_trace": [],
            "trace": {"actions": []},
            "kb_result": {"grounding_status": "ready", "answer_basis": "basis", "grounded_facts": [], "source_refs": [], "answer_evidence": {"schema_version": "answer-evidence/v1", "user_question": "q", "context_scope": "c", "acquisition_status": "ready", "answer_basis": "basis", "facts": [], "coverage": {"status": "none", "answered_parts": [], "missing_parts": [], "conflicts": [], "unresolved_constraints": []}, "calendar_facts": [], "policy_evidence": []}},
        }
        if self.final_result is not None:
            result["final_result"] = self.final_result
        return result


def test_ready_answer_forwards_profile_policy_evidence_to_writer(monkeypatch):
    direct = FakeDirectLLM()
    policy = SimpleNamespace(
        no_answer_policy_evidence=lambda: [{"text": "Используйте офис.", "source_ref": "profile:policy"}],
        finalize_simple_customer_text=lambda text, **kwargs: text,
    )
    turn = FakeTurnService(finalization_requested={"response_intent": "answer"})
    service = RoutingService(session_factory=lambda: FakeSession(), direct_llm=direct, policy=policy, turn_service=turn, answer_engine_mode="agent_tool_loop")
    monkeypatch.setattr("app.services.routing.build_context", lambda *args, **kwargs: {"recent_messages": [], "conversation_id": 1, "case_state": {"case_status": "open"}})
    monkeypatch.setattr("app.services.routing.validate_final_response", lambda result, **kwargs: result)
    monkeypatch.setattr("app.services.routing.build_audit_event", lambda *args, **kwargs: {"response_strategy": {"answer_engine": "agent_tool_loop"}})
    monkeypatch.setattr("app.services.routing.clean_llm_trace", lambda items: items)
    monkeypatch.setattr("app.services.routing.trace_metrics", lambda items: {})
    monkeypatch.setattr("app.services.routing.persist_workflow_event", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.routing.persist_outbound_message", lambda *args, **kwargs: None)

    service._handle_agent_tool_loop_inbound(FakeSession(), {"case_id": 1, "case_status": "open"}, SimpleNamespace(text="Вопрос", channel="tg", external_message_id="m1", external_event_id="e1"))

    assert len(direct.calls) == 1
    assert any(item.get("kind") == "profile_no_answer_option" for item in direct.calls[0]["tool_observations"])


def test_social_reply_does_not_forward_profile_policy_evidence(monkeypatch):
    direct = FakeDirectLLM()
    policy = SimpleNamespace(
        no_answer_policy_evidence=lambda: [{"text": "Используйте офис.", "source_ref": "profile:policy"}],
        finalize_simple_customer_text=lambda text, **kwargs: text,
    )
    turn = FakeTurnService(finalization_requested={"response_intent": "social_reply"})
    service = RoutingService(session_factory=lambda: FakeSession(), direct_llm=direct, policy=policy, turn_service=turn, answer_engine_mode="agent_tool_loop")
    monkeypatch.setattr("app.services.routing.build_context", lambda *args, **kwargs: {"recent_messages": [], "conversation_id": 1, "case_state": {"case_status": "open"}})
    monkeypatch.setattr("app.services.routing.validate_final_response", lambda result, **kwargs: result)
    monkeypatch.setattr("app.services.routing.build_audit_event", lambda *args, **kwargs: {"response_strategy": {"answer_engine": "agent_tool_loop"}})
    monkeypatch.setattr("app.services.routing.clean_llm_trace", lambda items: items)
    monkeypatch.setattr("app.services.routing.trace_metrics", lambda items: {})
    monkeypatch.setattr("app.services.routing.persist_workflow_event", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.routing.persist_outbound_message", lambda *args, **kwargs: None)

    service._handle_agent_tool_loop_inbound(FakeSession(), {"case_id": 1, "case_status": "open"}, SimpleNamespace(text="Вопрос", channel="tg", external_message_id="m1", external_event_id="e1"))

    assert len(direct.calls) == 1
    assert all(item.get("kind") != "profile_no_answer_option" for item in direct.calls[0]["tool_observations"])
