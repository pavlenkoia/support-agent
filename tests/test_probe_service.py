from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.deps import get_probe_service
from app.main import app
from app.services.probe_service import ProbeService
from tests.test_inbound_message import make_test_routing_service

client = TestClient(app)


class EmptyReplyRouting:
    def __init__(self, routing) -> None:
        self.routing = routing

    def handle_inbound(self, payload):
        result = self.routing.handle_inbound(payload)
        result["outcome"]["outcome_payload"]["response_text"] = ""
        return result

    def record_outbound_message(self, case_id: int, text: str) -> None:
        self.routing.record_outbound_message(case_id, text)

    @property
    def session_factory(self):
        return self.routing.session_factory


def make_probe_service(tmp_path: Path, *, routing=None) -> ProbeService:
    routing = routing or make_test_routing_service(tmp_path)
    return ProbeService(routing=routing, session_factory=routing.session_factory)




def test_probe_service_close_session_marks_case_resolved_and_logs_event(tmp_path: Path) -> None:
    service = make_probe_service(tmp_path)
    session_id = service.start_session(scenario_name="close-check", requested_by="igor")["session_id"]
    service.send_message(session_id, "Сколько стоят прыжки?")

    closed = service.close_session(session_id)
    trace = service.get_trace(session_id)

    assert closed["closed"] is True
    assert closed["case_status"] == "resolved"
    assert trace["events"][-1]["event_type"] == "probe_session_closed"


def test_probe_service_does_not_persist_empty_assistant_reply(tmp_path: Path) -> None:
    base_routing = make_test_routing_service(tmp_path)
    service = make_probe_service(tmp_path, routing=EmptyReplyRouting(base_routing))
    session_id = service.start_session(scenario_name="empty-reply", requested_by="igor")["session_id"]

    result = service.send_message(session_id, "Сколько стоят прыжки?")
    messages = service.get_messages(session_id)

    assert result["final_answer"] == ""
    assert [item["role"] for item in messages["messages"]] == ["user"]






class TraceFieldRouting:
    def __init__(self, routing) -> None:
        self.routing = routing

    def handle_inbound(self, payload):
        result = self.routing.handle_inbound(payload)
        result["route"]["outcome_kind"] = "grounded_answer"
        return result

    def record_outbound_message(self, case_id: int, text: str) -> None:
        self.routing.record_outbound_message(case_id, text)

    @property
    def session_factory(self):
        return self.routing.session_factory
