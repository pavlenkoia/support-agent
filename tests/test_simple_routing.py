from pathlib import Path

from app.core.db import Base, make_session_factory
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
