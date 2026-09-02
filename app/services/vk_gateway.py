from __future__ import annotations

import hashlib
import random
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update

from app.core.config import settings
from app.core.db import SessionLocal
from app.integrations.vk.client import VKAPIClient
from app.integrations.vk.sender import VKSender
from app.models.case import SupportCase
from app.models.conversation import Conversation
from app.models.conversation_transport_state import ConversationTransportState
from app.models.message import Message
from app.models.outbound_transport_send import OutboundTransportSend
from app.models.transport_event import TransportEvent
from app.models.user import User
from app.schemas.message import InboundMessage
from app.services.case_resolution import ensure_conversation, resolve_case
from app.services.inbound_queue import Generation, InboundQueue
from app.services.persistence import (
    activate_human_override,
    finalize_outbound_transport_send,
    find_recent_outbound_transport_match,
    find_transport_event,
    get_or_create_conversation_transport_state,
    is_override_active,
    mark_transport_event_failed,
    mark_transport_event_processed,
    mark_transport_event_retry_pending,
    persist_human_outbound_message,
    persist_inbound_message,
    persist_outbound_transport_send,
    persist_transport_event,
    persist_workflow_event,
    reconcile_outbound_transport_send,
    set_last_bot_reply,
    set_last_inbound_message,
)
from app.services.routing import RoutingService


class VKGatewayService:
    def __init__(
        self,
        *,
        routing: RoutingService | None = None,
        sender: VKSender | None = None,
        client: VKAPIClient | None = None,
        session_factory=SessionLocal,
        override_silence_seconds: int | None = None,
        queue: InboundQueue | None = None,
    ) -> None:
        self.routing = routing or RoutingService()
        self.sender = sender or VKSender()
        self.client = client or getattr(self.sender, "client", None) or VKAPIClient()
        self.session_factory = session_factory
        self.override_silence_seconds = override_silence_seconds or settings.vk_override_silence_seconds
        self.queue = queue or InboundQueue(
            self._process_generation,
            quiet_seconds=settings.inbound_coalesce_quiet_seconds,
            max_wait_seconds=settings.inbound_coalesce_max_wait_seconds,
            autostart=True,
        )

    def handle_event(self, event: dict[str, Any]) -> dict[str, Any]:
        event_type = str(event.get("type") or "")
        if event_type == "message_new":
            return self._handle_message_new(event)
        if event_type == "message_reply":
            return self._handle_message_reply(event)
        return {"ok": True, "ignored": True, "reason": "unsupported_event_type", "event_type": event_type}

    def _handle_message_new(self, event: dict[str, Any]) -> dict[str, Any]:
        message = self._extract_message(event)
        text = str(message.get("text") or "").strip()
        peer_id = self._string_id(message.get("peer_id"))
        from_id = self._string_id(message.get("from_id"))
        message_id = self._string_id(message.get("id"))
        if not text or not peer_id or not from_id or not message_id:
            return {"ok": True, "ignored": True, "reason": "unsupported_message_new"}
        event_time = self._event_time(message)
        dedupe_key = f"vk:message_new:{peer_id}:{message_id}"
        inbound = InboundMessage(
            channel="vk", external_user_id=from_id, external_chat_id=peer_id, text=text,
            external_message_id=message_id, external_event_type="message_new", external_event_id=dedupe_key,
            received_at=event_time, raw_event=event,
            metadata={"group_id": event.get("group_id"), "peer_id": peer_id, "from_id": from_id},
        )
        with self.session_factory() as session:
            transport_event, created = persist_transport_event(
                session, platform="vk", event_type="message_new", dedupe_key=dedupe_key, payload_json=event,
                external_event_id=dedupe_key, external_message_id=message_id,
                conversation_external_id=self._conversation_external_id(peer_id), received_at=event_time,
            )
            if not created:
                session.commit()
                return {"ok": True, "ignored": True, "reason": "duplicate_event", "event_type": "message_new"}
            self._sync_user_display_name(session, external_user_id=from_id)
            conversation = ensure_conversation(session, channel="vk", external_chat_id=peer_id)
            state = self._locked_transport_state(session, conversation_id=conversation.id)
            set_last_inbound_message(session, state, external_message_id=message_id)
            case = resolve_case(session, inbound)
            # Viewer history is an audit projection of accepted VK input, not a
            # record of only turns that reached automatic delivery.  Persist it
            # before queuing so a manual operator reply/cancel cannot erase the
            # customer's original question from the dialogue.
            persist_inbound_message(session, case["case_id"], inbound)
            if is_override_active(state, now=event_time):
                mark_transport_event_processed(session, transport_event, status="suppressed")
                session.commit()
                return {"ok": True, "ignored": False, "suppressed": True, "reason": "human_override_active", "case": case, "event_type": "message_new"}
            session.commit()
        batch = self.queue.submit(inbound)
        return {"ok": True, "ignored": False, "queued": True, "event_type": "message_new", "batch_id": batch.batch_id, "revision": batch.revision}

    def _process_generation(self, inbound: InboundMessage, generation: Generation) -> str | None:
        """Run one combined turn; raw events were persisted at ingress already."""
        try:
            result = self.routing.handle_inbound(inbound, persist_inbound=False)
        except Exception as exc:
            self._mark_generation_failed(generation, str(exc))
            raise
        if self._is_retry_pending(result):
            # A newer inbound message owns the next combined turn. Do not put an
            # obsolete subset onto the durable retry schedule: it would be stale
            # immediately and delay the newer customer message behind recovery.
            if not generation.is_current():
                return "superseded"
            self._mark_generation_retry_pending(generation, result)
            return "retry_pending"

        case = result.get("case") or {}
        case_id = case.get("case_id")
        reply_text = self._build_reply_text(result)
        if not reply_text:
            self._mark_generation_waiting_human(
                generation,
                case_id=case_id,
                reason="completed_without_customer_reply",
            )
            return "waiting_human"
        conversation_id = case.get("conversation_id")
        if case_id is None:
            return "retry_pending"

        def persist_and_deliver() -> str:
            with self.session_factory() as session:
                current_conversation_id = conversation_id
                if current_conversation_id is None:
                    conversation = session.scalar(
                        select(Conversation).where(Conversation.external_id == self._conversation_external_id(inbound.external_chat_id))
                    )
                    current_conversation_id = conversation.id if conversation is not None else None
                if current_conversation_id is None:
                    conversation = ensure_conversation(session, channel="vk", external_chat_id=inbound.external_chat_id)
                    current_conversation_id = conversation.id
                state = get_or_create_conversation_transport_state(
                    session, conversation_id=current_conversation_id, platform="vk"
                )
                # The queue lock prevents an in-process submit from changing
                # the generation revision while this final boundary runs, but
                # ingress persists the newest inbound first.  Consult that
                # durable marker immediately before journaling/sending so an
                # already-received newer customer message suppresses an older
                # generated reply rather than producing two bot replies.
                if self._is_stale_or_overridden(state, inbound, now=datetime.now(UTC)):
                    self._mark_generation_sources(session, generation, status="suppressed")
                    session.commit()
                    return "superseded"
                random_id = str(random.randint(1, 2_147_483_647))
                persist_outbound_transport_send(
                    session,
                    platform="vk",
                    conversation_id=current_conversation_id,
                    case_id=case_id,
                    peer_external_id=inbound.external_chat_id,
                    random_id=random_id,
                    content_text=reply_text,
                    send_status="pending",
                )
                # VK can emit message_reply before messages.send returns. Commit the
                # correlation record first so that event cannot be imported as human.
                session.commit()
                try:
                    delivery = self.sender.send_message(
                        peer_id=inbound.external_chat_id,
                        text=reply_text,
                        random_id=random_id,
                    )
                except Exception as exc:
                    self._mark_generation_sources(
                        session,
                        generation,
                        status="failed",
                    )
                    for source in generation.source_messages:
                        if source.external_event_id and (event := find_transport_event(session, source.external_event_id)) is not None:
                            event.error_text = f"delivery_exception:{type(exc).__name__}: {exc}"
                    finalize_outbound_transport_send(session, random_id=random_id, send_status="failed")
                    session.commit()
                    return "failed"
                sent = delivery.get("sent", delivery.get("ok"))
                self._mark_generation_sources(session, generation, status="processed" if sent else "failed")
                if not sent:
                    for source in generation.source_messages:
                        if source.external_event_id and (event := find_transport_event(session, source.external_event_id)) is not None:
                            event.error_text = self._format_delivery_error(delivery)
                    finalize_outbound_transport_send(session, random_id=random_id, send_status="failed")
                    session.commit()
                    return "retry_pending"
                finalize_outbound_transport_send(
                    session,
                    random_id=random_id,
                    external_message_id=delivery.get("external_message_id"),
                    sent_at=delivery.get("sent_at"),
                )
                set_last_bot_reply(session, state, replied_at=delivery.get("sent_at"))
                session.commit()
            self.routing.record_outbound_message(case_id, reply_text)
            return "delivered"

        return generation.run_if_current(persist_and_deliver)

    def recover_expired_received_events(self, *, now: datetime | None = None) -> dict[str, int]:
        current_time = now or datetime.now(UTC)
        cutoff = current_time - timedelta(seconds=settings.vk_received_event_timeout_seconds)
        with self.session_factory() as session:
            events = session.scalars(
                select(TransportEvent).where(
                    TransportEvent.platform == "vk",
                    TransportEvent.event_type == "message_new",
                    (
                        ((TransportEvent.status == "received") & (TransportEvent.received_at <= cutoff))
                        | ((TransportEvent.status == "processing") & (TransportEvent.available_at <= current_time))
                    ),
                )
            ).all()
            for event in events:
                mark_transport_event_retry_pending(
                    session,
                    event,
                    error_text="received_timeout_recovery",
                    available_at=current_time,
                )
            session.commit()
        return {"recovered": len(events)}

    def recover_legacy_timeout_events(self, *, now: datetime | None = None) -> dict[str, int]:
        current_time = now or datetime.now(UTC)
        with self.session_factory() as session:
            events = session.scalars(
                select(TransportEvent).where(
                    TransportEvent.platform == "vk",
                    TransportEvent.event_type == "message_new",
                    TransportEvent.status == "waiting_human",
                    TransportEvent.error_text == "received_timeout_without_finalization",
                )
            ).all()
            for event in events:
                mark_transport_event_retry_pending(
                    session,
                    event,
                    error_text="received_timeout_recovery",
                    available_at=current_time,
                )
                conversation = session.scalar(
                    select(Conversation).where(Conversation.external_id == event.conversation_external_id)
                )
                if conversation is not None:
                    support_case = session.scalar(
                        select(SupportCase)
                        .where(
                            SupportCase.conversation_id == conversation.id,
                            SupportCase.status == "waiting_human",
                            SupportCase.route_mode == "received_timeout_without_finalization",
                        )
                        .order_by(SupportCase.id.desc())
                    )
                    if support_case is not None:
                        support_case.status = "open"
                        support_case.route_mode = "retry_pending"
                        persist_workflow_event(
                            session,
                            support_case.id,
                            {"transport_event_id": event.id, "reason": "received_timeout_recovery"},
                            event_type="inbound_recovery_scheduled",
                            actor="system:vk_recovery",
                        )
            session.commit()
        return {"recovered": len(events)}

    def _mark_generation_sources(self, session, generation: Generation, *, status: str) -> None:
        for source in generation.source_messages:
            if source.external_event_id and (event := find_transport_event(session, source.external_event_id)) is not None:
                mark_transport_event_processed(session, event, status=status)

    def _mark_generation_failed(self, generation: Generation, error: str) -> None:
        with self.session_factory() as session:
            for source in generation.source_messages:
                if source.external_event_id and (event := find_transport_event(session, source.external_event_id)) is not None:
                    mark_transport_event_failed(session, event, error)
            session.commit()

    def _mark_generation_retry_pending(self, generation: Generation, result: dict[str, Any]) -> None:
        with self.session_factory() as session:
            for source in generation.source_messages:
                if source.external_event_id and (event := find_transport_event(session, source.external_event_id)) is not None:
                    self._schedule_retry_pending(session, event, result)
            session.commit()

    def _mark_generation_waiting_human(self, generation: Generation, *, case_id: int | None, reason: str) -> None:
        with self.session_factory() as session:
            self._mark_generation_sources(session, generation, status="waiting_human")
            for source in generation.source_messages:
                if source.external_event_id and (event := find_transport_event(session, source.external_event_id)) is not None:
                    event.error_text = reason
            if case_id is not None and (support_case := session.get(SupportCase, case_id)) is not None:
                support_case.status = "waiting_human"
                support_case.route_mode = reason
                persist_workflow_event(
                    session,
                    support_case.id,
                    {"reason": reason},
                    event_type="inbound_waiting_human",
                    actor="system:vk_gateway",
                )
            session.commit()

    def _mark_generation_sources_processed(self, generation: Generation) -> None:
        with self.session_factory() as session:
            self._mark_generation_sources(session, generation, status="processed")
            session.commit()

    def _process_message_new(
        self,
        event: dict[str, Any],
        *,
        inbound_override: InboundMessage | None = None,
        already_persisted: bool = False,
        generation: Generation | None = None,
    ) -> dict[str, Any]:
        message = self._extract_message(event)
        text = str(message.get("text") or "").strip()
        peer_id = self._string_id(message.get("peer_id"))
        from_id = self._string_id(message.get("from_id"))
        message_id = self._string_id(message.get("id"))
        if not text or not peer_id or not from_id or not message_id:
            return {"ok": True, "ignored": True, "reason": "unsupported_message_new"}

        event_time = self._event_time(message)
        dedupe_key = f"vk:message_new:{peer_id}:{message_id}"
        inbound = InboundMessage(
            channel="vk",
            external_user_id=from_id,
            external_chat_id=peer_id,
            text=text,
            external_message_id=message_id,
            external_event_type="message_new",
            external_event_id=dedupe_key,
            received_at=event_time,
            raw_event=event,
            metadata={
                "group_id": event.get("group_id"),
                "peer_id": peer_id,
                "from_id": from_id,
            },
        )
        if inbound_override is not None:
            inbound = inbound_override

        with self.session_factory() as session:
            transport_event, created = persist_transport_event(
                session,
                platform="vk",
                event_type="message_new",
                dedupe_key=dedupe_key,
                payload_json=event,
                external_event_id=dedupe_key,
                external_message_id=message_id,
                conversation_external_id=self._conversation_external_id(peer_id),
                received_at=event_time,
            )
            if not created and not already_persisted:
                session.commit()
                return {"ok": True, "ignored": True, "reason": "duplicate_event", "event_type": "message_new"}

            self._sync_user_display_name(session, external_user_id=from_id)
            conversation = ensure_conversation(session, channel="vk", external_chat_id=peer_id)
            state = self._locked_transport_state(session, conversation_id=conversation.id)
            set_last_inbound_message(session, state, external_message_id=message_id)

            if is_override_active(state, now=event_time):
                case = resolve_case(session, inbound)
                persist_inbound_message(session, case["case_id"], inbound)
                mark_transport_event_processed(session, transport_event, status="suppressed")
                session.commit()
                return {
                    "ok": True,
                    "ignored": False,
                    "suppressed": True,
                    "reason": "human_override_active",
                    "case": case,
                    "event_type": "message_new",
                }

            session.commit()

        try:
            result = self.routing.handle_inbound(inbound, persist_inbound=False)
        except Exception as exc:
            with self.session_factory() as session:
                stored_event = find_transport_event(session, dedupe_key)
                if stored_event is not None:
                    mark_transport_event_failed(session, stored_event, str(exc))
                session.commit()
            raise

        if generation is not None and not generation.is_current():
            return {"ok": True, "ignored": False, "suppressed": True, "reason": "generation_superseded", "app_result": result}

        if self._is_retry_pending(result):
            with self.session_factory() as session:
                stored_event = find_transport_event(session, dedupe_key)
                if stored_event is not None:
                    self._schedule_retry_pending(session, stored_event, result)
                session.commit()
            return {
                "ok": True,
                "ignored": False,
                "event_type": "message_new",
                "retry_pending": True,
                "app_result": result,
            }

        reply_text = self._build_reply_text(result)
        if not reply_text:
            with self.session_factory() as session:
                stored_event = find_transport_event(session, dedupe_key)
                if stored_event is not None:
                    mark_transport_event_processed(session, stored_event, status="processed")
                session.commit()
            return {"ok": True, "ignored": False, "reply_skipped": True, "app_result": result}

        case = result.get("case") or {}
        conversation_id = case.get("conversation_id")
        case_id = case.get("case_id")

        with self.session_factory() as session:
            if conversation_id is None:
                conversation = session.scalar(select(Conversation).where(Conversation.external_id == self._conversation_external_id(peer_id)))
                if conversation is not None:
                    conversation_id = conversation.id
            if conversation_id is None:
                conversation = ensure_conversation(session, channel="vk", external_chat_id=peer_id)
                conversation_id = conversation.id
            state = get_or_create_conversation_transport_state(session, conversation_id=conversation_id, platform="vk")
            if is_override_active(state, now=datetime.now(UTC)):
                stored_event = find_transport_event(session, dedupe_key)
                if stored_event is not None:
                    mark_transport_event_processed(session, stored_event, status="suppressed")
                session.commit()
                return {
                    "ok": True,
                    "ignored": False,
                    "suppressed": True,
                    "reason": "human_override_activated_before_send",
                    "app_result": result,
                }
            if generation is not None and case_id is not None:
                persist_inbound_message(session, case_id, inbound)
            session.commit()

        if generation is not None and not generation.is_current():
            return {"ok": True, "ignored": False, "suppressed": True, "reason": "generation_superseded", "app_result": result}
        delivery = self.sender.send_message(peer_id=peer_id, text=reply_text)
        sent = delivery.get("sent")
        if sent is None:
            sent = bool(delivery.get("ok"))

        with self.session_factory() as session:
            stored_event = find_transport_event(session, dedupe_key)
            if stored_event is not None:
                mark_transport_event_processed(session, stored_event, status="processed" if sent else "failed")
                if not sent:
                    stored_event.error_text = self._format_delivery_error(delivery)
            if sent:
                state = get_or_create_conversation_transport_state(session, conversation_id=conversation_id, platform="vk")
                persist_outbound_transport_send(
                    session,
                    platform="vk",
                    conversation_id=conversation_id,
                    case_id=case_id,
                    peer_external_id=peer_id,
                    random_id=str(delivery["random_id"]),
                    content_text=reply_text,
                    external_message_id=delivery.get("external_message_id"),
                    sent_at=delivery.get("sent_at"),
                )
                set_last_bot_reply(session, state, replied_at=delivery.get("sent_at"))
            session.commit()

        if sent and case_id is not None:
            self.routing.record_outbound_message(case_id, reply_text)

        return {
            "ok": True,
            "ignored": False,
            "event_type": "message_new",
            "reply_text": reply_text,
            "delivery": delivery,
            "app_result": result,
            "suppressed": False,
        }

    def process_due_retries(self, *, now: datetime | None = None) -> dict[str, int]:
        due_at = self._as_utc(now or datetime.now(UTC))
        with self.session_factory() as session:
            pending_events = list(
                session.scalars(
                    select(TransportEvent)
                    .where(
                        TransportEvent.platform == "vk",
                        TransportEvent.event_type == "message_new",
                        TransportEvent.status == "retry_pending",
                    )
                    .order_by(TransportEvent.conversation_external_id, TransportEvent.received_at, TransportEvent.id)
                )
            )

        grouped_event_ids: dict[str, list[int]] = {}
        grouped_available_at: dict[str, datetime] = {}
        for event in pending_events:
            conversation_key = str(event.conversation_external_id or event.id)
            grouped_event_ids.setdefault(conversation_key, []).append(event.id)
            grouped_available_at[conversation_key] = self._as_utc(event.available_at)

        processed = 0
        for conversation_key, event_ids in grouped_event_ids.items():
            # The newest unanswered customer message owns the retry schedule.
            # Older unanswered messages travel with it so their order and meaning
            # cannot disappear when a newer message supersedes an old generation.
            if grouped_available_at[conversation_key] > due_at:
                continue
            processed += self._process_retry_event(
                event_ids[-1], source_event_ids=event_ids, now=due_at
            )
        return {"processed": processed}

    def _process_retry_event(
        self,
        event_id: int,
        *,
        now: datetime,
        source_event_ids: list[int] | None = None,
    ) -> int:
        claimed_event_ids = list(dict.fromkeys(source_event_ids or [event_id]))
        claimed_count = len(claimed_event_ids)
        with self.session_factory() as session:
            claimed = session.execute(
                update(TransportEvent)
                .where(TransportEvent.id.in_(claimed_event_ids), TransportEvent.status == "retry_pending")
                .values(status="processing", available_at=now + timedelta(seconds=settings.vk_received_event_timeout_seconds))
            )
            if claimed.rowcount != len(claimed_event_ids):
                session.rollback()
                return 0
            session.commit()
            events = self._load_retry_events(session, claimed_event_ids)
            event = next((item for item in events if item.id == event_id), None)
            if event is None or len(events) != len(claimed_event_ids):
                return claimed_count
            inbound = self._combined_retry_inbound(events)
            random_id = str(self._retry_random_id(event.id))
            existing_send = session.scalar(
                select(OutboundTransportSend).where(OutboundTransportSend.random_id == random_id)
            )
            replay_send = (
                {
                    "content_text": existing_send.content_text,
                    "conversation_id": existing_send.conversation_id,
                    "case_id": existing_send.case_id,
                    "peer_external_id": existing_send.peer_external_id,
                }
                if existing_send is not None
                and existing_send.send_status == "pending"
                and existing_send.content_text
                else None
            )
            conversation = session.scalar(
                select(Conversation).where(Conversation.external_id == event.conversation_external_id)
            )
            state = (
                get_or_create_conversation_transport_state(session, conversation_id=conversation.id, platform="vk")
                if conversation is not None
                else None
            )
            if self._is_stale_or_overridden(state, inbound, now=now):
                for source_event in events:
                    mark_transport_event_processed(session, source_event, status="suppressed")
                    source_event.error_text = "vk_retry_suppressed"
                session.commit()
                return claimed_count

        if replay_send is None:
            try:
                result = self.routing.handle_inbound(inbound, persist_inbound=False)
            except Exception as exc:
                with self.session_factory() as session:
                    for source_event in self._load_retry_events(session, claimed_event_ids):
                        self._schedule_retry_pending(
                            session,
                            source_event,
                            {"route": {"route_reason": f"retry_runtime_error:{type(exc).__name__}"}},
                            now=now,
                        )
                    session.commit()
                return claimed_count

            if self._is_retry_pending(result):
                with self.session_factory() as session:
                    for source_event in self._load_retry_events(session, claimed_event_ids):
                        self._schedule_retry_pending(session, source_event, result, now=now)
                    session.commit()
                return claimed_count

            reply_text = self._build_reply_text(result)
        else:
            result = {
                "case": {
                    "conversation_id": replay_send["conversation_id"],
                    "case_id": replay_send["case_id"],
                }
            }
            reply_text = str(replay_send["content_text"])
        with self.session_factory() as session:
            events = self._load_retry_events(session, claimed_event_ids)
            event = next((item for item in events if item.id == event_id), None)
            if event is None or len(events) != len(claimed_event_ids):
                return claimed_count
            if not reply_text:
                for source_event in events:
                    mark_transport_event_failed(session, source_event, "retry_completed_without_customer_reply")
                session.commit()
                return claimed_count

            peer_id = self._string_id(self._extract_message(event.payload_json).get("peer_id"))
            conversation_id = (result.get("case") or {}).get("conversation_id")
            if conversation_id is None:
                conversation = session.scalar(select(Conversation).where(Conversation.external_id == self._conversation_external_id(peer_id)))
                conversation_id = conversation.id if conversation is not None else None
            if conversation_id is None:
                for source_event in events:
                    mark_transport_event_failed(session, source_event, "retry_completed_without_conversation")
                session.commit()
                return claimed_count
            case_id = (result.get("case") or {}).get("case_id")
            state = get_or_create_conversation_transport_state(session, conversation_id=conversation_id, platform="vk")
            if self._is_stale_or_overridden(state, inbound, now=now):
                for source_event in events:
                    mark_transport_event_processed(session, source_event, status="suppressed")
                    source_event.error_text = "vk_retry_suppressed"
                session.commit()
                return claimed_count
            random_id = str(self._retry_random_id(event.id))
            existing_send = session.scalar(select(OutboundTransportSend).where(OutboundTransportSend.random_id == random_id))
            if existing_send is None:
                if case_id is None:
                    for source_event in events:
                        mark_transport_event_failed(session, source_event, "retry_completed_without_case")
                    session.commit()
                    return claimed_count
                # Inbound messages were durably projected at VK ingress.  Only
                # journal the retry reply here; replaying the source turn must
                # not duplicate Viewer history.
                persist_outbound_transport_send(
                    session,
                    platform="vk",
                    conversation_id=conversation_id,
                    case_id=case_id,
                    peer_external_id=peer_id,
                    random_id=random_id,
                    content_text=reply_text,
                    send_status="pending",
                )
            session.commit()

        try:
            delivery = self._send_retry_if_current(
                event_id=event_id,
                source_event_ids=claimed_event_ids,
                conversation_id=conversation_id,
                inbound=inbound,
                peer_id=peer_id,
                reply_text=reply_text,
                random_id=random_id,
                now=now,
            )
        except Exception as exc:
            with self.session_factory() as session:
                for source_event in self._load_retry_events(session, claimed_event_ids):
                    self._schedule_retry_pending(
                        session,
                        source_event,
                        {"route": {"route_reason": f"vk_send_exception:{type(exc).__name__}"}},
                        now=now,
                    )
                session.commit()
            return claimed_count
        if delivery is None:
            return claimed_count
        sent = delivery.get("sent")
        if sent is None:
            sent = bool(delivery.get("ok"))
        with self.session_factory() as session:
            events = self._load_retry_events(session, claimed_event_ids)
            if len(events) != len(claimed_event_ids):
                return claimed_count
            for source_event in events:
                mark_transport_event_processed(session, source_event, status="processed" if sent else "failed")
            if not sent:
                for source_event in events:
                    source_event.error_text = self._format_delivery_error(delivery)
                finalize_outbound_transport_send(session, random_id=random_id, send_status="failed")
            else:
                state = get_or_create_conversation_transport_state(session, conversation_id=conversation_id, platform="vk")
                finalize_outbound_transport_send(
                    session,
                    random_id=random_id,
                    external_message_id=delivery.get("external_message_id"),
                    sent_at=delivery.get("sent_at"),
                )
                set_last_bot_reply(session, state, replied_at=delivery.get("sent_at"))
            session.commit()
        if sent and (case_id := (result.get("case") or {}).get("case_id")) is not None:
            self.routing.record_outbound_message(case_id, reply_text)
        return claimed_count

    def _locked_transport_state(self, session, *, conversation_id: int) -> ConversationTransportState:
        state = get_or_create_conversation_transport_state(session, conversation_id=conversation_id, platform="vk")
        locked = session.scalar(
            select(ConversationTransportState)
            .where(ConversationTransportState.id == state.id)
            .with_for_update()
        )
        if locked is None:
            raise LookupError(f"conversation transport state disappeared: {conversation_id}")
        return locked

    def _send_retry_if_current(
        self,
        *,
        event_id: int,
        source_event_ids: list[int],
        conversation_id: int,
        inbound: InboundMessage,
        peer_id: str,
        reply_text: str,
        random_id: str,
        now: datetime,
    ) -> dict[str, Any] | None:
        """Last serialized stale gate immediately before the external VK send."""
        with self.session_factory() as session:
            event = session.get(TransportEvent, event_id)
            state = self._locked_transport_state(session, conversation_id=conversation_id)
            if event is None or self._is_stale_or_overridden(state, inbound, now=now):
                for source_event in self._load_retry_events(session, source_event_ids):
                    mark_transport_event_processed(session, source_event, status="suppressed")
                    source_event.error_text = "vk_retry_suppressed"
                finalize_outbound_transport_send(session, random_id=random_id, send_status="failed")
                session.commit()
                return None
            # Hold the conversation-state lock until messages.send returns. A new
            # inbound or human override must serialize after this accepted send.
            return self.sender.send_message(peer_id=peer_id, text=reply_text, random_id=random_id)

    @staticmethod
    def _load_retry_events(session, event_ids: list[int]) -> list[TransportEvent]:
        return list(
            session.scalars(
                select(TransportEvent)
                .where(TransportEvent.id.in_(event_ids))
                .order_by(TransportEvent.received_at, TransportEvent.id)
            )
        )

    def _combined_retry_inbound(self, events: list[TransportEvent]) -> InboundMessage:
        source_messages = [self._inbound_from_event(event.payload_json) for event in events]
        first = source_messages[0]
        latest = source_messages[-1]
        return first.model_copy(
            update={
                "text": "\n".join(message.text for message in source_messages),
                "received_at": latest.received_at,
                "external_message_id": latest.external_message_id,
                "external_event_id": None,
                "raw_event": latest.raw_event,
                "metadata": {"retry_source_event_count": len(source_messages)},
            }
        )

    def _is_stale_or_overridden(self, state, inbound: InboundMessage, *, now: datetime) -> bool:
        if state is not None and is_override_active(state, now=now):
            return True
        if inbound.external_message_id is None or state is None or state.last_inbound_external_message_id is None:
            return False
        return str(state.last_inbound_external_message_id) != str(inbound.external_message_id)

    @staticmethod
    def _retry_random_id(event_id: int) -> int:
        """Stable VK idempotency key: recovery repeats the same external send."""
        value = int.from_bytes(hashlib.sha256(f"support-agent-vk-retry:{event_id}".encode()).digest()[:4], "big")
        return (value % 2_147_483_647) + 1

    def _schedule_retry_pending(self, session, event: TransportEvent, result: dict[str, Any], *, now: datetime | None = None) -> None:
        route = result.get("route") or {}
        reason = str(route.get("route_reason") or "kb_agent_transport_failure")
        mark_transport_event_retry_pending(
            session,
            event,
            error_text=reason,
            available_at=(now or datetime.now(UTC)) + timedelta(seconds=settings.kb_agent_deferred_retry_delay_seconds),
        )

    @staticmethod
    def _is_retry_pending(result: dict[str, Any]) -> bool:
        outcome = result.get("outcome") or {}
        return str(outcome.get("outcome_type") or "") == "retry_pending"

    def _inbound_from_event(self, event: dict[str, Any]) -> InboundMessage:
        message = self._extract_message(event)
        peer_id = self._string_id(message.get("peer_id"))
        from_id = self._string_id(message.get("from_id"))
        message_id = self._string_id(message.get("id"))
        return InboundMessage(
            channel="vk",
            external_user_id=from_id,
            external_chat_id=peer_id,
            text=str(message.get("text") or "").strip(),
            external_message_id=message_id,
            external_event_type="message_new",
            external_event_id=f"vk:message_new:{peer_id}:{message_id}",
            received_at=self._event_time(message),
            raw_event=event,
            metadata={"group_id": event.get("group_id"), "peer_id": peer_id, "from_id": from_id},
        )

    def _handle_message_reply(self, event: dict[str, Any]) -> dict[str, Any]:
        message = self._extract_message(event)
        peer_id = self._string_id(message.get("peer_id"))
        message_id = self._string_id(message.get("id"))
        text = str(message.get("text") or "").strip()
        if not peer_id or not message_id:
            return {"ok": True, "ignored": True, "reason": "unsupported_message_reply"}

        event_time = self._event_time(message)
        dedupe_key = f"vk:message_reply:{peer_id}:{message_id}"

        with self.session_factory() as session:
            transport_event, created = persist_transport_event(
                session,
                platform="vk",
                event_type="message_reply",
                dedupe_key=dedupe_key,
                payload_json=event,
                external_event_id=dedupe_key,
                external_message_id=message_id,
                conversation_external_id=self._conversation_external_id(peer_id),
                received_at=event_time,
            )
            if not created:
                session.commit()
                return {"ok": True, "ignored": True, "reason": "duplicate_event", "event_type": "message_reply"}

            conversation = ensure_conversation(session, channel="vk", external_chat_id=peer_id)
            state = get_or_create_conversation_transport_state(session, conversation_id=conversation.id, platform="vk")
            matched = find_recent_outbound_transport_match(
                session,
                platform="vk",
                peer_external_id=peer_id,
                external_message_id=message_id,
                content_text=text,
                event_time=event_time,
            )
            if matched is not None:
                reconcile_outbound_transport_send(session, matched, external_message_id=message_id, reconciled_at=event_time)
                mark_transport_event_processed(session, transport_event)
                session.commit()
                return {
                    "ok": True,
                    "ignored": False,
                    "event_type": "message_reply",
                    "sent_by": "bot",
                    "matched_send_id": matched.id,
                }

            self._recover_missing_inbound_context(session, peer_id=peer_id, conversation=conversation)
            persist_human_outbound_message(
                session,
                conversation_id=conversation.id,
                text=text,
                sent_at=event_time,
            )
            activate_human_override(
                session,
                state,
                admin_replied_at=event_time,
                silence_seconds=self.override_silence_seconds,
            )
            self.queue.cancel("vk", peer_id)
            mark_transport_event_processed(session, transport_event)
            session.commit()
            return {
                "ok": True,
                "ignored": False,
                "event_type": "message_reply",
                "sent_by": "admin",
                "override_until": state.human_override_until.isoformat() if state.human_override_until else None,
            }

    def _recover_missing_inbound_context(self, session, *, peer_id: str, conversation: Conversation) -> None:
        has_inbound = session.scalar(
            select(Message.id)
            .join(SupportCase, SupportCase.id == Message.case_id)
            .where(SupportCase.conversation_id == conversation.id, Message.role == "user")
            .limit(1)
        )
        if has_inbound is not None:
            return

        response = self.client.get_history(peer_id, count=20)
        if not response.get("ok"):
            return
        payload = response.get("response") or {}
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return

        inbound_items = [
            item
            for item in items
            if isinstance(item, dict)
            and not bool(item.get("out"))
            and self._string_id(item.get("peer_id")) == peer_id
            and self._string_id(item.get("from_id"))
            and str(item.get("text") or "").strip()
        ]
        if not inbound_items:
            return

        item = max(inbound_items, key=lambda candidate: int(candidate.get("date") or 0))
        from_id = self._string_id(item.get("from_id"))
        self._sync_user_display_name(session, external_user_id=from_id)
        inbound = InboundMessage(
            channel="vk",
            external_user_id=from_id,
            external_chat_id=peer_id,
            text=str(item["text"]).strip(),
            external_message_id=self._string_id(item.get("id")),
            external_event_type="history_recovery",
            external_event_id=f"vk:history_recovery:{peer_id}:{self._string_id(item.get('id'))}",
            received_at=self._event_time(item),
            raw_event={"type": "history_recovery", "object": {"message": item}},
            metadata={"peer_id": peer_id, "from_id": from_id},
        )
        support_case = session.scalar(
            select(SupportCase)
            .where(SupportCase.conversation_id == conversation.id)
            .order_by(SupportCase.id.desc())
        )
        if support_case is None:
            support_case = SupportCase(conversation_id=conversation.id, status="open", route_mode="history_recovery")
            session.add(support_case)
            session.flush()
        persist_inbound_message(session, support_case.id, inbound)

    def _sync_user_display_name(self, session, *, external_user_id: str) -> None:
        normalized_user_id = self._string_id(external_user_id)
        if not self._is_vk_person_user_id(normalized_user_id):
            return

        user_external_id = f"vk:{normalized_user_id}"
        user = session.scalar(select(User).where(User.external_id == user_external_id))
        if user is not None and str(user.display_name or "").strip():
            return

        profile = self._lookup_vk_user_profile(normalized_user_id)
        if profile is None:
            return

        display_name = self._compose_vk_display_name(profile)
        if not display_name:
            return

        if user is None:
            user = User(external_id=user_external_id, display_name=display_name)
            session.add(user)
        else:
            user.display_name = display_name
        session.flush()

    def _lookup_vk_user_profile(self, external_user_id: str) -> dict[str, Any] | None:
        response = self.client.get_users([external_user_id])
        if not response.get("ok"):
            return None
        profiles = response.get("response")
        if not isinstance(profiles, list) or not profiles:
            return None
        profile = profiles[0]
        return profile if isinstance(profile, dict) else None

    @staticmethod
    def _is_vk_person_user_id(external_user_id: str) -> bool:
        try:
            return int(external_user_id) > 0
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _compose_vk_display_name(profile: dict[str, Any]) -> str | None:
        first_name = str(profile.get("first_name") or "").strip()
        last_name = str(profile.get("last_name") or "").strip()
        display_name = " ".join(part for part in (first_name, last_name) if part).strip()
        return display_name or None

    @staticmethod
    def _format_delivery_error(delivery: dict[str, Any]) -> str:
        reason = str(delivery.get("reason") or "vk_send_failed")
        response = delivery.get("vk_response")
        error_payload = response.get("error") if isinstance(response, dict) else None
        if not isinstance(error_payload, dict):
            return reason

        parts = [reason]
        error_code = error_payload.get("error_code")
        error_msg = error_payload.get("error_msg")
        if error_code is not None:
            parts.append(f"error_code={error_code}")
        if error_msg:
            parts.append(f"error_msg={error_msg}")
        return "; ".join(parts)

    @staticmethod
    def _extract_message(event: dict[str, Any]) -> dict[str, Any]:
        obj = event.get("object") or {}
        message = obj.get("message") if isinstance(obj, dict) else None
        if isinstance(message, dict):
            return message
        return obj if isinstance(obj, dict) else {}

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _string_id(value: Any) -> str:
        if value is None:
            return ""
        return str(value)

    @staticmethod
    def _event_time(message: dict[str, Any]) -> datetime:
        raw = message.get("date")
        if raw is None:
            return datetime.now(UTC)
        return datetime.fromtimestamp(int(raw), tz=UTC)

    @staticmethod
    def _build_reply_text(result: dict[str, Any]) -> str:
        outcome = result.get("outcome") or {}
        payload = outcome.get("outcome_payload") or {}
        return str(payload.get("response_text") or "").strip()

    @staticmethod
    def _conversation_external_id(peer_id: str) -> str:
        return f"vk:{peer_id}"
