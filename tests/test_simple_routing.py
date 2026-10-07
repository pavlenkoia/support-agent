import json
from pathlib import Path

from sqlalchemy import select
from test_jev_answer_engine import FACTS
from test_jev_answer_engine import engine as make_jev_engine

from app.core.db import Base, make_session_factory
from app.models.workflow_event import WorkflowEvent
from app.schemas.message import InboundMessage
from app.services.routing import RoutingService
from app.workers.main import process_once


class RecordingSimpleEngine:
    def __init__(self, *, kind: str = "grounded_answer", response_text: str = "Подтверждённый ответ.") -> None:
        self.kind = kind
        self.response_text = response_text
        self.calls: list[dict] = []

    def answer(self, **kwargs: object) -> dict:
        self.calls.append(kwargs)
        return {
            "kind": self.kind,
            "response_text": self.response_text,
            "source_refs": ["price:01"] if self.kind == "grounded_answer" else [],
            "telemetry": {"answer_engine": "simple_full_corpus_natural", "logical_llm_call_count": 1},
        }


def test_routing_uses_single_supported_engine_and_bounded_history(tmp_path: Path) -> None:
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'routing.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    engine = RecordingSimpleEngine()
    routing = RoutingService(session_factory=session_factory, simple_answer_engine=engine)

    first = routing.handle_inbound(InboundMessage(channel="internal_test", external_user_id="igor", external_chat_id="igor", text="Первый вопрос"))
    routing.record_outbound_message(first["case"]["case_id"], "Здравствуйте! Подтверждённый ответ.")
    routing.handle_inbound(InboundMessage(channel="internal_test", external_user_id="igor", external_chat_id="igor", text="Второй вопрос"))

    assert first["route"]["answer_engine"] == "simple_full_corpus_natural"
    assert len(engine.calls) == 2
    assert engine.calls[1]["history"] == [
        {"role": "user", "content": "Первый вопрос"},
        {"role": "assistant", "content": "Здравствуйте! Подтверждённый ответ."},
    ]


def test_routing_rejects_retired_engine_mode(tmp_path: Path) -> None:
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'retired.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])

    try:
        RoutingService(session_factory=session_factory, answer_engine_mode="agent_tool_loop")
    except ValueError as exc:
        assert str(exc) == "unsupported answer engine: agent_tool_loop"
    else:
        raise AssertionError("retired engine mode must be rejected")


def test_opt_in_jev_route_uses_selected_engine_and_keeps_delivery_isolated(tmp_path: Path) -> None:
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'jev.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    engine = RecordingSimpleEngine(kind="cannot_answer", response_text="Для уточнения вопроса можно позвонить в офис в рабочее время.")
    routing = RoutingService(session_factory=session_factory, simple_answer_engine=engine, answer_engine_mode="jev_selected_fact")
    result = routing.handle_inbound(InboundMessage(channel="internal_test", external_user_id="igor", external_chat_id="igor", text="Неизвестный вопрос"))
    assert result["route"]["answer_engine"] == "jev_selected_fact"
    assert result["outcome"]["outcome_type"] == "cannot_answer"
    assert result["outcome"]["outcome_payload"]["response_text"] == "Для уточнения вопроса можно позвонить в офис в рабочее время."
    assert len(engine.calls) == 1


def test_jev_no_answer_keeps_exact_text_on_first_reply(tmp_path: Path) -> None:
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'jev-fallback.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    fallback = (
        "К сожалению, я не могу ответить на ваш вопрос. Я уже подключил к решению специалиста. "
        "Он подготовит ответ и напишет вам в этом чате."
    )
    engine = RecordingSimpleEngine(kind="cannot_answer", response_text=fallback)
    routing = RoutingService(session_factory=session_factory, simple_answer_engine=engine, answer_engine_mode="jev_selected_fact")
    result = routing.handle_inbound(InboundMessage(channel="internal_test", external_user_id="igor", external_chat_id="igor", text="Неизвестный вопрос"))
    assert result["outcome"]["outcome_payload"]["response_text"] == fallback


def test_jev_selected_fact_snapshot_is_persisted_for_later_review(tmp_path: Path) -> None:
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'jev-telemetry.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    jev, _, _ = make_jev_engine(tmp_path, ["substantive", "jump.announcement"],
        [json.dumps({"response_text": "Дата будет подтверждена в анонсе."}, ensure_ascii=False)])
    routing = RoutingService(session_factory=session_factory, simple_answer_engine=jev, answer_engine_mode="jev_selected_fact")
    result = routing.handle_inbound(InboundMessage(channel="internal_test", external_user_id="igor", external_chat_id="igor", text="Когда прыжки?"))
    with session_factory() as session:
        events = session.scalars(select(WorkflowEvent).where(WorkflowEvent.case_id == result["case"]["case_id"])).all()
    snapshots = [event.payload for event in events if event.event_type == "jev_fact_selected"]
    assert len(snapshots) == 1
    assert snapshots[0]["selected_fact"] == FACTS[0]
    assert snapshots[0]["kb_sha256"] == result["audit"]["response_strategy"]["kb_sha256"]
    assert snapshots[0]["trace_id"] == result["audit"]["trace_packet"]["trace_id"]
    assert result["audit"]["response_strategy"]["turn_type"] == "substantive"
    assert result["audit"]["trace_packet"]["source_refs"] == ["jump.announcement"]


def test_jev_fact_snapshot_is_persisted_when_writer_fails(tmp_path: Path) -> None:
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'jev-error.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    jev, _, _ = make_jev_engine(tmp_path, ["substantive", "jump.announcement"], ['{"response_text": invalid}'])
    routing = RoutingService(session_factory=session_factory, simple_answer_engine=jev, answer_engine_mode="jev_selected_fact")
    result = routing.handle_inbound(InboundMessage(channel="internal_test", external_user_id="igor", external_chat_id="igor", text="Когда прыжки?"))
    with session_factory() as session:
        events = session.scalars(select(WorkflowEvent).where(WorkflowEvent.case_id == result["case"]["case_id"])).all()
    snapshots = [event.payload for event in events if event.event_type == "jev_fact_selected"]
    assert result["route"]["route"] == "retry_pending"
    assert len(snapshots) == 1
    assert snapshots[0]["selected_fact"] == FACTS[0]
    assert snapshots[0]["trace_id"] == result["audit"]["trace_packet"]["trace_id"]
    assert result["audit"]["response_strategy"]["contract_error"].startswith("jev_engine:fact_writer:")


def test_worker_process_once_invokes_due_retry_without_consuming_fake_gateway_state() -> None:
    class FakeGateway:
        def __init__(self) -> None:
            self.processed = 0
            self.handled: list[dict] = []

        def process_due_retries(self) -> int:
            self.processed += 1
            return 1

        def handle_update(self, update: dict) -> None:
            self.handled.append(update)

    class FakePoller:
        def get_updates(self, offset=None, timeout=None):
            return {"result": []}

    class FakeAcker:
        def ack(self, offset: int) -> None:
            raise AssertionError("no update means no acknowledgement")

    result = process_once(FakeGateway(), FakePoller(), FakeAcker(), offset=10)

    assert result == {"processed": 0, "retry_processed": 1, "next_offset": 10}
