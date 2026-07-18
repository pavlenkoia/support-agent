from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.conversation_transport_state import ConversationTransportState
from app.models.message import Message
from app.models.outbound_transport_send import OutboundTransportSend
from app.models.transport_event import TransportEvent
from app.models.workflow_event import WorkflowEvent
from app.models.case import SupportCase
from app.models.viewer_notification_outbox import ViewerNotificationOutbox
from app.core.config import settings
from app.schemas.message import InboundMessage


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_timestamp(value: datetime | None) -> datetime:
    if value is None:
        return utc_now()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def hash_text(text: str) -> str:
    return hashlib.sha256(str(text or "").strip().encode("utf-8")).hexdigest()


def persist_inbound_message(session: Session, case_id: int, payload: InboundMessage) -> Message:
    message = Message(case_id=case_id, role="user", content=payload.text)
    session.add(message)
    session.flush()
    if settings.viewer_push_enabled and payload.channel == "vk":
        conversation_id = session.scalar(select(SupportCase.conversation_id).where(SupportCase.id == case_id))
        if conversation_id is not None:
            session.add(
                ViewerNotificationOutbox(
                    event_type="viewer_user_message_received",
                    conversation_id=conversation_id,
                    message_id=message.id,
                )
            )
            session.flush()
    return message


def persist_outbound_message(session: Session, case_id: int, text: str, *, role: str = "assistant") -> Message:
    message = Message(case_id=case_id, role=role, content=text)
    session.add(message)
    session.flush()
    return message


def persist_human_outbound_message(
    session: Session,
    *,
    conversation_id: int,
    text: str,
    sent_at: datetime | None = None,
) -> Message:
    support_case = session.scalar(
        select(SupportCase)
        .where(SupportCase.conversation_id == conversation_id)
        .order_by(SupportCase.id.desc())
    )
    if support_case is None:
        support_case = SupportCase(conversation_id=conversation_id, status="open", route_mode="human_override")
        session.add(support_case)
        session.flush()

    message = Message(
        case_id=support_case.id,
        role="human",
        content=str(text or "").strip(),
        created_at=normalize_timestamp(sent_at),
    )
    session.add(message)
    session.flush()
    return message


def persist_workflow_event(
    session: Session,
    case_id: int,
    payload: dict,
    *,
    event_type: str = "inbound_processed",
    actor: str = "system:routing",
) -> WorkflowEvent:
    event = WorkflowEvent(
        case_id=case_id,
        event_type=event_type,
        actor=actor,
        payload=payload,
    )
    session.add(event)
    session.flush()
    return event


def find_transport_event(session: Session, dedupe_key: str) -> TransportEvent | None:
    return session.scalar(select(TransportEvent).where(TransportEvent.dedupe_key == dedupe_key))


def persist_transport_event(
    session: Session,
    *,
    platform: str,
    event_type: str,
    dedupe_key: str,
    payload_json: dict[str, Any],
    external_event_id: str | None = None,
    external_message_id: str | None = None,
    conversation_external_id: str | None = None,
    received_at: datetime | None = None,
) -> tuple[TransportEvent, bool]:
    existing = find_transport_event(session, dedupe_key)
    if existing is not None:
        return existing, False

    event = TransportEvent(
        platform=platform,
        event_type=event_type,
        external_event_id=external_event_id,
        external_message_id=external_message_id,
        conversation_external_id=conversation_external_id,
        dedupe_key=dedupe_key,
        status="received",
        payload_json=payload_json,
        received_at=normalize_timestamp(received_at),
    )
    session.add(event)
    session.flush()
    return event, True


def mark_transport_event_processed(session: Session, event: TransportEvent, *, status: str = "processed") -> TransportEvent:
    event.status = status
    event.processed_at = utc_now()
    session.flush()
    return event


def mark_transport_event_failed(session: Session, event: TransportEvent, error_text: str) -> TransportEvent:
    event.status = "failed"
    event.error_text = error_text
    event.processed_at = utc_now()
    session.flush()
    return event


def persist_outbound_transport_send(
    session: Session,
    *,
    platform: str,
    peer_external_id: str,
    random_id: str,
    content_text: str,
    conversation_id: int | None = None,
    case_id: int | None = None,
    external_message_id: str | None = None,
    sent_at: datetime | None = None,
    send_status: str = "sent",
) -> OutboundTransportSend:
    record = OutboundTransportSend(
        platform=platform,
        conversation_id=conversation_id,
        case_id=case_id,
        peer_external_id=peer_external_id,
        random_id=random_id,
        external_message_id=external_message_id,
        content_hash=hash_text(content_text),
        content_text=content_text,
        sent_by="bot",
        send_status=send_status,
        sent_at=normalize_timestamp(sent_at),
    )
    session.add(record)
    session.flush()
    return record


def reconcile_outbound_transport_send(
    session: Session,
    record: OutboundTransportSend,
    *,
    external_message_id: str | None = None,
    reconciled_at: datetime | None = None,
) -> OutboundTransportSend:
    if external_message_id:
        record.external_message_id = str(external_message_id)
    record.send_status = "reconciled"
    record.reconciled_at = normalize_timestamp(reconciled_at)
    session.flush()
    return record


def find_recent_outbound_transport_match(
    session: Session,
    *,
    platform: str,
    peer_external_id: str,
    external_message_id: str | None = None,
    content_text: str | None = None,
    event_time: datetime | None = None,
    within_seconds: int = 300,
) -> OutboundTransportSend | None:
    if external_message_id:
        matched = session.scalar(
            select(OutboundTransportSend).where(
                OutboundTransportSend.platform == platform,
                OutboundTransportSend.peer_external_id == peer_external_id,
                OutboundTransportSend.external_message_id == str(external_message_id),
            )
        )
        if matched is not None:
            return matched

    if not content_text:
        return None

    query = select(OutboundTransportSend).where(
        OutboundTransportSend.platform == platform,
        OutboundTransportSend.peer_external_id == peer_external_id,
        OutboundTransportSend.content_hash == hash_text(content_text),
    )
    if event_time is not None:
        event_dt = normalize_timestamp(event_time)
        query = query.where(
            OutboundTransportSend.sent_at >= event_dt - timedelta(seconds=within_seconds),
            OutboundTransportSend.sent_at <= event_dt + timedelta(seconds=within_seconds),
        )
    query = query.order_by(OutboundTransportSend.sent_at.desc())
    return session.scalar(query)


def get_or_create_conversation_transport_state(
    session: Session,
    *,
    conversation_id: int,
    platform: str,
) -> ConversationTransportState:
    state = session.scalar(
        select(ConversationTransportState).where(ConversationTransportState.conversation_id == conversation_id)
    )
    if state is None:
        state = ConversationTransportState(conversation_id=conversation_id, platform=platform)
        session.add(state)
        session.flush()
    return state


def is_override_active(state: ConversationTransportState | None, *, now: datetime | None = None) -> bool:
    if state is None or state.human_override_until is None:
        return False
    return normalize_timestamp(now) < normalize_timestamp(state.human_override_until)


def set_last_inbound_message(
    session: Session,
    state: ConversationTransportState,
    *,
    external_message_id: str | None,
) -> ConversationTransportState:
    state.last_inbound_external_message_id = str(external_message_id) if external_message_id is not None else None
    state.updated_at = utc_now()
    session.flush()
    return state


def set_last_bot_reply(
    session: Session,
    state: ConversationTransportState,
    *,
    replied_at: datetime | None = None,
) -> ConversationTransportState:
    state.last_bot_reply_at = normalize_timestamp(replied_at)
    state.updated_at = utc_now()
    session.flush()
    return state


def activate_human_override(
    session: Session,
    state: ConversationTransportState,
    *,
    admin_replied_at: datetime | None = None,
    silence_seconds: int = 3600,
) -> ConversationTransportState:
    replied_at = normalize_timestamp(admin_replied_at)
    state.last_admin_reply_at = replied_at
    state.human_override_until = replied_at + timedelta(seconds=silence_seconds)
    state.updated_at = utc_now()
    session.flush()
    return state
