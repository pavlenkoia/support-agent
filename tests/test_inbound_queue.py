from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

from app.schemas.message import InboundMessage
from app.services.inbound_queue import InboundQueue


class RecordingProcessor:
    def __init__(self) -> None:
        self.calls: list[tuple[InboundMessage, object]] = []

    def __call__(self, inbound: InboundMessage, generation) -> str:
        self.calls.append((inbound, generation))
        return "delivered"


def inbound(*, text: str, message_id: str) -> InboundMessage:
    return InboundMessage(
        channel="telegram",
        external_user_id="700",
        external_chat_id="100",
        external_message_id=message_id,
        text=text,
    )


def test_flushes_one_message_after_quiet_window() -> None:
    now = datetime(2026, 8, 11, tzinfo=UTC)
    processor = RecordingProcessor()
    queue = InboundQueue(processor, quiet_seconds=5, max_wait_seconds=15)

    queue.submit(inbound(text="Здравствуйте", message_id="1"), now=now)

    assert queue.flush_due(now=now + timedelta(seconds=4), background=False) == 0
    assert processor.calls == []

    assert queue.flush_due(now=now + timedelta(seconds=5), background=False) == 1
    assert [call[0].text for call in processor.calls] == ["Здравствуйте"]


def test_second_message_extends_quiet_window_and_combines_in_arrival_order() -> None:
    now = datetime(2026, 8, 11, tzinfo=UTC)
    processor = RecordingProcessor()
    queue = InboundQueue(processor, quiet_seconds=5, max_wait_seconds=15)

    queue.submit(inbound(text="Здравствуйте", message_id="1"), now=now)
    queue.submit(inbound(text="Сколько стоит?", message_id="2"), now=now + timedelta(seconds=4))

    assert queue.flush_due(now=now + timedelta(seconds=5), background=False) == 0
    assert queue.flush_due(now=now + timedelta(seconds=9), background=False) == 1
    assert processor.calls[0][0].text == "Здравствуйте\nСколько стоит?"
    assert [message.external_message_id for message in processor.calls[0][1].source_messages] == ["1", "2"]


def test_human_cancel_removes_pending_batch_and_prevents_processing() -> None:
    now = datetime(2026, 8, 11, tzinfo=UTC)
    processor = RecordingProcessor()
    queue = InboundQueue(processor, quiet_seconds=5, max_wait_seconds=15)

    queue.submit(inbound(text="Подожду", message_id="1"), now=now)
    queue.cancel("telegram", "100")

    assert queue.batch_count == 0
    assert queue.flush_due(now=now + timedelta(seconds=20), background=False) == 0
    assert processor.calls == []


def test_flushes_at_max_wait_despite_new_messages() -> None:
    now = datetime(2026, 8, 11, tzinfo=UTC)
    processor = RecordingProcessor()
    queue = InboundQueue(processor, quiet_seconds=5, max_wait_seconds=15)

    queue.submit(inbound(text="Первая", message_id="1"), now=now)
    queue.submit(inbound(text="Вторая", message_id="2"), now=now + timedelta(seconds=12))

    assert queue.flush_due(now=now + timedelta(seconds=14), background=False) == 0
    assert queue.flush_due(now=now + timedelta(seconds=15), background=False) == 1
    assert processor.calls[0][0].text == "Первая\nВторая"
    assert processor.calls[0][0].external_message_id == "2"


def test_new_message_during_generation_supersedes_old_result_and_replays_full_batch() -> None:
    now = datetime(2026, 8, 11, tzinfo=UTC)
    started = threading.Event()
    release = threading.Event()
    delivered: list[str] = []

    def processor(payload: InboundMessage, generation) -> str:
        if payload.text == "Первая":
            started.set()
            assert release.wait(timeout=2)
        result = generation.run_if_current(lambda: delivered.append(payload.text) or "delivered")
        return result or "superseded"

    queue = InboundQueue(processor, quiet_seconds=5, max_wait_seconds=15)
    queue.submit(inbound(text="Первая", message_id="1"), now=now)
    assert queue.flush_due(now=now + timedelta(seconds=5), background=True) == 1
    assert started.wait(timeout=2)
    queue.submit(inbound(text="Вторая", message_id="2"), now=now + timedelta(seconds=6))
    release.set()

    deadline = threading.Event()
    deadline.wait(0.05)
    assert delivered == []
    assert queue.flush_due(now=now + timedelta(seconds=11), background=False) == 1
    assert delivered == ["Первая\nВторая"]


def test_human_cancel_during_generation_suppresses_result() -> None:
    now = datetime(2026, 8, 11, tzinfo=UTC)
    started = threading.Event()
    release = threading.Event()
    delivered: list[str] = []

    def processor(payload: InboundMessage, generation) -> str:
        started.set()
        assert release.wait(timeout=2)
        return generation.run_if_current(lambda: delivered.append(payload.text) or "delivered") or "superseded"

    queue = InboundQueue(processor, quiet_seconds=5, max_wait_seconds=15)
    queue.submit(inbound(text="Жду ответ", message_id="1"), now=now)
    queue.flush_due(now=now + timedelta(seconds=5), background=True)
    assert started.wait(timeout=2)
    queue.cancel("telegram", "100")
    release.set()
    threading.Event().wait(0.05)

    assert delivered == []
    assert queue.batch_count == 0


def test_retry_pending_batch_does_not_spin_until_a_new_revision_arrives() -> None:
    now = datetime(2026, 8, 11, tzinfo=UTC)
    def processor(_: InboundMessage, __) -> str:
        return "retry_pending"

    queue = InboundQueue(processor, quiet_seconds=5, max_wait_seconds=15)
    queue.submit(inbound(text="Первый", message_id="1"), now=now)

    assert queue.flush_due(now=now + timedelta(seconds=5), background=False) == 1
    assert queue.flush_due(now=now + timedelta(seconds=20), background=False) == 0
    queue.submit(inbound(text="Уточнение", message_id="2"), now=now + timedelta(seconds=21))
    assert queue.flush_due(now=now + timedelta(seconds=26), background=False) == 1


def test_newer_message_unblocks_a_generation_that_becomes_retry_pending() -> None:
    now = datetime(2026, 8, 11, tzinfo=UTC)
    calls: list[str] = []
    queue: InboundQueue

    def processor(payload: InboundMessage, _) -> str:
        calls.append(payload.text)
        if payload.text == "Первый":
            queue.submit(inbound(text="Уточнение", message_id="2"), now=now + timedelta(seconds=1))
            return "retry_pending"
        return "delivered"

    queue = InboundQueue(processor, quiet_seconds=0, max_wait_seconds=0)
    queue.submit(inbound(text="Первый", message_id="1"), now=now)

    assert queue.flush_due(now=now, background=False) == 1
    assert queue.flush_due(now=now + timedelta(seconds=1), background=False) == 1
    assert calls == ["Первый", "Первый\nУточнение"]


def test_different_dialogs_flush_independently() -> None:
    now = datetime(2026, 8, 11, tzinfo=UTC)
    processor = RecordingProcessor()
    queue = InboundQueue(processor, quiet_seconds=5, max_wait_seconds=15)
    queue.submit(inbound(text="Первый", message_id="1"), now=now)
    queue.submit(inbound(text="Второй", message_id="2").model_copy(update={"external_chat_id": "200"}), now=now)

    assert queue.flush_due(now=now + timedelta(seconds=5), background=False) == 2
    assert {payload.external_chat_id for payload, _ in processor.calls} == {"100", "200"}
