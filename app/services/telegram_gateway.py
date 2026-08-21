from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.config import parse_csv_set, settings
from app.core.db import SessionLocal
from app.integrations.telegram.client import TelegramBotClient
from app.models.message import Message
from app.models.transport_event import TransportEvent
from app.schemas.message import InboundMessage
from app.services.case_resolution import ensure_conversation
from app.services.inbound_queue import Generation, InboundQueue
from app.services.persistence import (
    find_transport_event,
    get_or_create_conversation_transport_state,
    is_override_active,
    mark_transport_event_processed,
    mark_transport_event_retry_pending,
    persist_transport_event,
    set_last_inbound_message,
)
from app.services.routing import RoutingService


class TelegramGatewayService:
    def __init__(
        self,
        routing: RoutingService | None = None,
        sender: TelegramBotClient | None = None,
        allowed_chat_ids: set[str] | None = None,
        typing_interval_seconds: float = 4.0,
        session_factory=None,
        queue: InboundQueue | None = None,
    ) -> None:
        self.routing = routing or RoutingService()
        self.sender = sender or TelegramBotClient(token=settings.telegram_bot_token)
        self.allowed_chat_ids = allowed_chat_ids if allowed_chat_ids is not None else parse_csv_set(settings.telegram_allowed_chats)
        self.typing_interval_seconds = typing_interval_seconds
        self.session_factory = session_factory or getattr(self.routing, "session_factory", None) or SessionLocal
        self.queue = queue or InboundQueue(
            self._process_generation,
            quiet_seconds=settings.inbound_coalesce_quiet_seconds,
            max_wait_seconds=settings.inbound_coalesce_max_wait_seconds,
            autostart=True,
        )

    def handle_update(self, update: dict) -> dict:
        message = update.get("message") or update.get("edited_message") or {}
        text = (message.get("text") or "").strip()
        chat_id = str((message.get("chat") or {}).get("id", ""))
        user_id = str((message.get("from") or {}).get("id", ""))
        message_id = str(message.get("message_id") or "")
        if not text or not chat_id or not user_id or not message_id:
            return {"ok": True, "ignored": True, "reason": "unsupported_update"}
        if self.allowed_chat_ids and chat_id not in self.allowed_chat_ids:
            return {"ok": True, "ignored": True, "reason": "chat_not_allowed", "chat_id": chat_id}

        inbound = InboundMessage(
            channel="telegram",
            external_user_id=user_id,
            external_chat_id=chat_id,
            text=text,
            external_message_id=message_id,
            external_event_type="message",
            external_event_id=f"telegram:message:{chat_id}:{message_id}",
            raw_event=update,
        )
        if text == "/new":
            self.queue.cancel("telegram", chat_id)
            reset_result = self.routing.reset_session(inbound)
            reply_text = "Сессию сбросил. Начинаем заново — можете отправить новый запрос."
            delivery = self.sender.send_message(chat_id, reply_text)
            return {"ok": True, "ignored": False, "update_id": update.get("update_id"), "reply_text": reply_text, "delivery": delivery, "app_result": {"command": "/new", "reset": reset_result}}

        if not self._persist_raw_event(inbound):
            return {"ok": True, "ignored": True, "reason": "duplicate_event"}
        self._update_transport_state_on_inbound(inbound)
        batch = self.queue.submit(inbound)
        return {"ok": True, "ignored": False, "queued": True, "update_id": update.get("update_id"), "batch_id": batch.batch_id, "revision": batch.revision}

    def flush_due(self) -> int:
        return self.queue.flush_due()

    def process_due_retries(self, *, now=None) -> int:
        if not hasattr(self.routing, "session_factory"):
            return 0
        due_at = now or datetime.now(UTC)
        with self.session_factory() as session:
            events = list(
                session.scalars(
                    select(TransportEvent).where(
                        TransportEvent.platform == "telegram",
                        TransportEvent.event_type == "message",
                        TransportEvent.status == "retry_pending",
                        TransportEvent.available_at <= due_at,
                    )
                    .order_by(TransportEvent.available_at, TransportEvent.id)
                )
            )
        processed = 0
        for event in events:
            result = self._retry_transport_event(event.id, due_at=due_at)
            if result != "noop":
                processed += 1
        return processed

    def _process_generation(self, inbound: InboundMessage, generation: Generation) -> str | None:
        return self._run_with_typing(inbound.external_chat_id, lambda: self._generate_and_deliver(inbound, generation))

    def _generate_and_deliver(
        self,
        inbound: InboundMessage,
        generation: Generation,
        *,
        retry_event_id: int | None = None,
        due_at: datetime | None = None,
    ) -> str | None:
        if hasattr(self.routing, "session_factory") and inbound.external_event_id is not None:
            with self.session_factory() as session:
                state = self._conversation_state(session, inbound.external_chat_id)
                if self._is_stale_or_overridden(session, state, inbound, due_at=due_at):
                    if retry_event_id is not None and (event := session.get(TransportEvent, retry_event_id)) is not None:
                        event.status = "suppressed"
                        event.error_text = event.error_text or "telegram_retry_suppressed"
                        session.commit()
                    return "suppressed"
        try:
            result = self.routing.handle_inbound(inbound, persist_inbound=False)
        except Exception as exc:
            self._mark_retry_pending(inbound, f"routing_error:{type(exc).__name__}", generation=generation, now=due_at)
            return "retry_pending"
        reply_text = self._build_reply_text(result)
        if not reply_text:
            self._mark_retry_pending(inbound, "empty_reply", generation=generation, now=due_at)
            return "retry_pending"
        case_id = (result.get("case") or {}).get("case_id")
        if case_id is None:
            self._mark_retry_pending(inbound, "missing_case", generation=generation, now=due_at)
            return "retry_pending"

        def persist_and_deliver() -> str:
            if hasattr(self.routing, "session_factory"):
                with self.session_factory() as check_session:
                    state = self._conversation_state(check_session, inbound.external_chat_id)
                    if self._is_stale_or_overridden(check_session, state, inbound, due_at=due_at):
                        if retry_event_id is not None and (event := check_session.get(TransportEvent, retry_event_id)) is not None:
                            event.status = "suppressed"
                            event.error_text = event.error_text or "telegram_retry_suppressed"
                            check_session.commit()
                        return "suppressed"
            if not self._has_persisted_user_message(case_id, inbound.text):
                self.routing.persist_inbound_message(case_id, inbound)
            delivery = self.sender.send_message(inbound.external_chat_id, reply_text)
            if not delivery.get("sent", delivery.get("ok")):
                self._mark_retry_pending(
                    inbound,
                    str(delivery.get("error") or "delivery_failed"),
                    generation=generation,
                    now=due_at,
                )
                return "retry_pending"
            self.routing.record_outbound_message(case_id, reply_text)
            self._mark_sources_processed(generation)
            return "delivered"

        if retry_event_id is not None:
            return persist_and_deliver()
        return generation.run_if_current(persist_and_deliver)

    def _run_with_typing(self, chat_id: str, callback: Callable[[], str | None]) -> str | None:
        self.sender.send_chat_action(chat_id, "typing")
        stop_event = threading.Event()

        def keepalive() -> None:
            while not stop_event.wait(self.typing_interval_seconds):
                self.sender.send_chat_action(chat_id, "typing")

        worker = threading.Thread(target=keepalive, name=f"telegram-typing-{chat_id}", daemon=True)
        worker.start()
        try:
            return callback()
        finally:
            stop_event.set()
            worker.join(timeout=self.typing_interval_seconds + 0.5)

    def _persist_raw_event(self, inbound: InboundMessage) -> bool:
        if not hasattr(self.routing, "session_factory"):
            return True
        with self.session_factory() as session:
            event, created = persist_transport_event(
                session,
                platform="telegram",
                event_type="message",
                dedupe_key=str(inbound.external_event_id),
                payload_json=inbound.raw_event or {},
                external_event_id=inbound.external_event_id,
                external_message_id=inbound.external_message_id,
                conversation_external_id=f"telegram:{inbound.external_chat_id}",
                received_at=inbound.received_at,
            )
            if created:
                event.available_at = self._retry_available_at(inbound.received_at, 0)
            session.commit()
            return created

    def _mark_sources_processed(self, generation: Generation) -> None:
        if not hasattr(self.routing, "session_factory"):
            return
        with self.session_factory() as session:
            for source in generation.source_messages:
                if source.external_event_id and (event := find_transport_event(session, source.external_event_id)) is not None:
                    mark_transport_event_processed(session, event)
            session.commit()

    def _mark_retry_pending(
        self,
        inbound: InboundMessage,
        error_text: str,
        *,
        generation: Generation | None = None,
        now: datetime | None = None,
    ) -> None:
        if not hasattr(self.routing, "session_factory"):
            return
        with self.session_factory() as session:
            sources = generation.source_messages if generation is not None else (inbound,)
            for source in sources:
                if source.external_event_id is None:
                    continue
                event = find_transport_event(session, str(source.external_event_id))
                if event is None:
                    event, _ = persist_transport_event(
                        session,
                        platform="telegram",
                        event_type="message",
                        dedupe_key=str(source.external_event_id),
                        payload_json=source.raw_event or {},
                        external_event_id=source.external_event_id,
                        external_message_id=source.external_message_id,
                        conversation_external_id=f"telegram:{source.external_chat_id}",
                        received_at=source.received_at,
                    )
                mark_transport_event_retry_pending(
                    session,
                    event,
                    error_text=error_text,
                    available_at=self._retry_available_at(now or datetime.now(UTC), event.retry_attempts + 1),
                )
            session.commit()

    def _retry_transport_event(self, event_id: int, *, due_at: datetime) -> str:
        with self.session_factory() as session:
            event = session.get(TransportEvent, event_id)
            if event is None or event.status != "retry_pending":
                return "noop"
            if self._normalized_dt(event.available_at) > self._normalized_dt(due_at):
                return "noop"
            payload = event.payload_json or {}
            message = payload.get("message") or payload.get("edited_message") or {}
            inbound = InboundMessage(
                channel="telegram",
                external_user_id=str((message.get("from") or {}).get("id", "")),
                external_chat_id=str((message.get("chat") or {}).get("id", "")),
                text=str(message.get("text") or "").strip(),
                external_message_id=str(message.get("message_id") or ""),
                external_event_type="message",
                external_event_id=str(event.external_event_id or event.dedupe_key),
                raw_event=payload,
            )
            generation = Generation(self.queue, (inbound.channel, inbound.external_chat_id), event.retry_attempts or 1, (inbound,))
            state = self._conversation_state(session, inbound.external_chat_id)
            if self._is_stale_or_overridden(session, state, inbound, due_at=due_at):
                event.status = "suppressed"
                event.error_text = event.error_text or "telegram_retry_suppressed"
                session.commit()
                return "suppressed"
        return self._generate_and_deliver(inbound, generation, retry_event_id=event.id, due_at=due_at)

    @staticmethod
    def _build_reply_text(result: dict) -> str:
        outcome = result.get("outcome", {})
        payload = outcome.get("outcome_payload", {})
        response_text = payload.get("response_text") if isinstance(payload, dict) else None
        return str(response_text or "").strip()

    @staticmethod
    def _normalized_dt(value: datetime | None) -> datetime:
        current = value or datetime.now(UTC)
        return current.replace(tzinfo=UTC) if current.tzinfo is None else current.astimezone(UTC)

    def _retry_available_at(self, received_at: datetime | None, retry_attempts: int) -> datetime:
        base = self._normalized_dt(received_at)
        _ = retry_attempts
        return base + timedelta(seconds=settings.kb_agent_deferred_retry_delay_seconds)

    def _conversation_state(self, session, external_chat_id: str):
        conversation = ensure_conversation(session, channel="telegram", external_chat_id=external_chat_id)
        return get_or_create_conversation_transport_state(session, conversation_id=conversation.id, platform="telegram")

    def _update_transport_state_on_inbound(self, inbound: InboundMessage) -> None:
        if not hasattr(self.routing, "session_factory"):
            return
        with self.session_factory() as session:
            state = self._conversation_state(session, inbound.external_chat_id)
            set_last_inbound_message(session, state, external_message_id=inbound.external_message_id)
            session.commit()

    def _is_stale_or_overridden(self, session, state, inbound: InboundMessage, *, due_at: datetime | None = None) -> bool:
        if is_override_active(state, now=due_at):
            return True
        if inbound.external_message_id is None:
            return False
        if state is None or state.last_inbound_external_message_id is None:
            return False
        return str(state.last_inbound_external_message_id) != str(inbound.external_message_id)

    def _has_persisted_user_message(self, case_id: int, text: str) -> bool:
        if not hasattr(self.routing, "session_factory"):
            return False
        with self.session_factory() as session:
            return bool(
                session.scalar(
                    select(Message.id).where(Message.case_id == case_id, Message.role == "user", Message.content == text).limit(1)
                )
            )
