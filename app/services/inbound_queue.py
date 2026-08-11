from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.schemas.message import InboundMessage

logger = logging.getLogger(__name__)

ConversationKey = tuple[str, str]
Processor = Callable[[InboundMessage, "Generation"], str | None]


@dataclass
class Batch:
    batch_id: str
    key: ConversationKey
    messages: list[InboundMessage]
    opened_at: datetime
    last_message_at: datetime
    revision: int = 1
    active_revision: int | None = None
    cancelled: bool = False
    retry_pending: bool = False


class Generation:
    def __init__(
        self,
        queue: "InboundQueue",
        key: ConversationKey,
        revision: int,
        source_messages: tuple[InboundMessage, ...],
    ) -> None:
        self._queue = queue
        self.key = key
        self.revision = revision
        self.source_messages = source_messages

    def is_current(self) -> bool:
        return self._queue.is_current(self.key, self.revision)

    def complete(self) -> None:
        self._queue.complete(self.key, self.revision)

    def run_if_current(self, action: Callable[[], str | None]) -> str | None:
        """Run the final persistence/delivery boundary atomically with supersession."""
        return self._queue.run_if_current(self.key, self.revision, action)


class InboundQueue:
    """In-memory, channel-independent coalescing queue keyed by conversation."""

    def __init__(
        self,
        processor: Processor,
        *,
        quiet_seconds: float,
        max_wait_seconds: float,
        autostart: bool = False,
    ) -> None:
        self.processor = processor
        self.quiet = timedelta(seconds=quiet_seconds)
        self.max_wait = timedelta(seconds=max_wait_seconds)
        self._batches: dict[ConversationKey, Batch] = {}
        self._lock = threading.RLock()
        if autostart:
            threading.Thread(target=self._scheduler, name="inbound-coalescer", daemon=True).start()

    def submit(self, inbound: InboundMessage, *, now: datetime | None = None) -> Batch:
        received_at = self._normalized_now(now)
        key = (inbound.channel, inbound.external_chat_id)
        with self._lock:
            batch = self._batches.get(key)
            if batch is None:
                batch = Batch(
                    batch_id=str(uuid.uuid4()),
                    key=key,
                    messages=[inbound],
                    opened_at=received_at,
                    last_message_at=received_at,
                )
                self._batches[key] = batch
                self._log("inbound_batch_opened", batch)
            else:
                batch.messages.append(inbound)
                batch.last_message_at = received_at
                batch.revision += 1
                batch.retry_pending = False
                self._log("inbound_message_coalesced", batch)
                if batch.active_revision is not None:
                    self._log("generation_superseded", batch, generation_revision=batch.active_revision)
            return batch

    def flush_due(self, *, now: datetime | None = None, background: bool = True) -> int:
        due_at = self._normalized_now(now)
        generations: list[Generation] = []
        with self._lock:
            for batch in self._batches.values():
                if batch.cancelled or batch.retry_pending or batch.active_revision is not None:
                    continue
                quiet_due = batch.last_message_at + self.quiet
                max_due = batch.opened_at + self.max_wait
                if due_at < quiet_due and due_at < max_due:
                    continue
                batch.active_revision = batch.revision
                reason = "quiet_window" if quiet_due <= max_due and due_at >= quiet_due else "max_wait"
                self._log("inbound_batch_flushed", batch, flush_reason=reason)
                generations.append(Generation(self, batch.key, batch.revision, tuple(batch.messages)))

        for generation in generations:
            if background:
                threading.Thread(target=self._run, args=(generation,), daemon=True).start()
            else:
                self._run(generation)
        return len(generations)

    def cancel(self, channel: str, external_chat_id: str) -> None:
        key = (channel, external_chat_id)
        with self._lock:
            batch = self._batches.get(key)
            if batch is None:
                return
            batch.cancelled = True
            self._log("inbound_batch_cancelled_by_human", batch)
            if batch.active_revision is None:
                del self._batches[key]

    @property
    def batch_count(self) -> int:
        with self._lock:
            return len(self._batches)

    def is_current(self, key: ConversationKey, revision: int) -> bool:
        with self._lock:
            batch = self._batches.get(key)
            return bool(batch and not batch.cancelled and batch.revision == revision and batch.active_revision == revision)

    def run_if_current(self, key: ConversationKey, revision: int, action: Callable[[], str | None]) -> str | None:
        """Serialize the irreversible final boundary against submit()/cancel().

        Routing runs without the queue lock. The short final DB/send section is
        linearized with new inbound events and human cancellation, so a result
        cannot be persisted or delivered after its revision became stale.
        """
        with self._lock:
            batch = self._batches.get(key)
            if batch is None or batch.cancelled or batch.revision != revision or batch.active_revision != revision:
                return "superseded"
            return action()

    def source_messages(self, key: ConversationKey, revision: int) -> tuple[InboundMessage, ...]:
        with self._lock:
            batch = self._batches.get(key)
            if batch is None or batch.revision < revision:
                return ()
            return tuple(batch.messages)

    def complete(self, key: ConversationKey, revision: int) -> None:
        with self._lock:
            batch = self._batches.get(key)
            if batch is None or batch.cancelled or batch.revision != revision:
                return
            self._log("inbound_batch_delivered", batch)
            del self._batches[key]

    def _run(self, generation: Generation) -> None:
        with self._lock:
            batch = self._batches.get(generation.key)
            if batch is None or batch.cancelled or batch.revision != generation.revision:
                return
            inbound = self._combined_inbound(batch)
        result = self.processor(inbound, generation)
        if result == "delivered":
            generation.complete()
        with self._lock:
            batch = self._batches.get(generation.key)
            if batch is not None and batch.active_revision == generation.revision:
                batch.active_revision = None
                if result == "retry_pending":
                    batch.retry_pending = True
                if batch.cancelled:
                    del self._batches[generation.key]

    def _scheduler(self) -> None:
        while True:
            self.flush_due()
            threading.Event().wait(0.1)

    @staticmethod
    def _combined_inbound(batch: Batch) -> InboundMessage:
        first = batch.messages[0]
        return first.model_copy(update={
            "text": "\n".join(message.text for message in batch.messages),
            "received_at": batch.messages[-1].received_at,
            "external_message_id": None,
            "external_event_id": None,
            "raw_event": None,
            "metadata": {"batch_id": batch.batch_id, "source_event_count": len(batch.messages)},
        })

    @staticmethod
    def _normalized_now(now: datetime | None) -> datetime:
        value = now or datetime.now(UTC)
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @staticmethod
    def _log(event: str, batch: Batch, **extra: object) -> None:
        logger.info(
            event,
            extra={
                "channel": batch.key[0],
                "external_chat_id": batch.key[1],
                "batch_id": batch.batch_id,
                "revision": batch.revision,
                "source_event_count": len(batch.messages),
                "opened_at": batch.opened_at.isoformat(),
                "last_message_at": batch.last_message_at.isoformat(),
                **extra,
            },
        )
