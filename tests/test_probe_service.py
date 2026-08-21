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


def test_probe_api_exposes_internal_session_lifecycle(tmp_path: Path) -> None:
    service = make_probe_service(tmp_path)
    app.dependency_overrides[get_probe_service] = lambda: service
    try:
        started = client.post(
            "/api/internal/probe/sessions",
            json={"scenario_name": "api-smoke", "requested_by": "igor"},
        )
        assert started.status_code == 200
        session_id = started.json()["session_id"]

        sent = client.post(
            f"/api/internal/probe/sessions/{session_id}/messages",
            json={"text": "25 июня прыжки будут?"},
        )
        assert sent.status_code == 200
        assert sent.json()["route"]["route"] == "answer"

        history = client.get(f"/api/internal/probe/sessions/{session_id}/messages")
        assert history.status_code == 200
        assert len(history.json()["messages"]) == 2

        waited = client.post(
            f"/api/internal/probe/sessions/{session_id}/wait-reply",
            json={"timeout_sec": 5},
        )
        assert waited.status_code == 200
        assert waited.json()["received"] is True
        assert waited.json()["final_answer"]

        detail = client.get(f"/api/internal/probe/sessions/{session_id}")
        assert detail.status_code == 200
        assert detail.json()["session_id"] == session_id
        assert detail.json()["message_count"] == 2

        trace = client.get(f"/api/internal/probe/sessions/{session_id}/trace")
        assert trace.status_code == 200
        assert trace.json()["is_test"] is True

        listing = client.get("/api/internal/probe/sessions", params={"requested_by": "igor"})
        assert listing.status_code == 200
        payload = listing.json()
        assert payload["total"] == 1
        assert payload["items"][0]["session_id"] == session_id
        assert payload["items"][0]["message_count"] == 2

        closed = client.post(f"/api/internal/probe/sessions/{session_id}/close")
        assert closed.status_code == 200
        assert closed.json()["closed"] is True

        resolved_listing = client.get("/api/internal/probe/sessions", params={"status": "resolved"})
        assert resolved_listing.status_code == 200
        assert resolved_listing.json()["items"][0]["case_status"] == "resolved"
    finally:
        app.dependency_overrides.clear()


def test_probe_api_lists_and_filters_sessions_by_db_markers(tmp_path: Path) -> None:
    service = make_probe_service(tmp_path)
    first_session = service.start_session(scenario_name="pricing", requested_by="igor")["session_id"]
    service.send_message(first_session, "Сколько стоят прыжки?")

    second_session = service.start_session(scenario_name="weather", requested_by="anna")["session_id"]
    service.send_message(second_session, "25 июня прыжки будут?")
    service.close_session(second_session)

    third_session = service.start_session(scenario_name="pending", requested_by="igor")["session_id"]

    app.dependency_overrides[get_probe_service] = lambda: service
    try:
        all_sessions = client.get("/api/internal/probe/sessions")
        assert all_sessions.status_code == 200
        payload = all_sessions.json()
        assert payload["total"] == 3
        session_ids = [item["session_id"] for item in payload["items"]]
        assert first_session in session_ids
        assert second_session in session_ids
        assert third_session in session_ids

        pricing_only = client.get("/api/internal/probe/sessions", params={"scenario_name": "pricing"})
        assert pricing_only.status_code == 200
        assert pricing_only.json()["total"] == 1
        assert pricing_only.json()["items"][0]["session_id"] == first_session

        anna_only = client.get("/api/internal/probe/sessions", params={"requested_by": "anna"})
        assert anna_only.status_code == 200
        assert anna_only.json()["total"] == 1
        assert anna_only.json()["items"][0]["session_id"] == second_session

        open_only = client.get("/api/internal/probe/sessions", params={"status": "open"})
        assert open_only.status_code == 200
        assert open_only.json()["total"] == 1
        assert open_only.json()["items"][0]["session_id"] == third_session
        assert open_only.json()["items"][0]["message_count"] == 0

        resolved_only = client.get("/api/internal/probe/sessions", params={"status": "resolved"})
        assert resolved_only.status_code == 200
        assert resolved_only.json()["total"] == 2
        resolved_ids = [item["session_id"] for item in resolved_only.json()["items"]]
        assert first_session in resolved_ids
        assert second_session in resolved_ids
    finally:
        app.dependency_overrides.clear()


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
