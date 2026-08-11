from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.case import SupportCase
from app.models.channel import ChannelAccount
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.schemas.message import InboundMessage

PROBE_MARKER_KEYS = ("is_test", "source", "session_type", "scenario_name", "requested_by")
SESSION_TIMEZONE = ZoneInfo("Asia/Yekaterinburg")
SESSION_INACTIVITY_LIMIT = timedelta(hours=2)


def _extract_session_markers(payload: InboundMessage) -> dict:
    metadata = payload.metadata or {}
    return {
        "is_test": bool(metadata.get("is_test", False)),
        "source": metadata.get("source"),
        "session_type": metadata.get("session_type"),
        "scenario_name": metadata.get("scenario_name"),
        "requested_by": metadata.get("requested_by"),
    }


def _apply_conversation_markers(conversation: Conversation, markers: dict) -> None:
    conversation.is_test = bool(markers.get("is_test", False))
    conversation.source = markers.get("source")
    conversation.session_type = markers.get("session_type")
    conversation.scenario_name = markers.get("scenario_name")
    conversation.requested_by = markers.get("requested_by")


def _apply_case_markers(support_case: SupportCase, markers: dict) -> None:
    support_case.is_test = bool(markers.get("is_test", False))
    support_case.source = markers.get("source")
    support_case.session_type = markers.get("session_type")
    support_case.scenario_name = markers.get("scenario_name")
    support_case.requested_by = markers.get("requested_by")


def _normalized_timestamp(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _starts_new_session(latest_message: Message | None, payload: InboundMessage) -> bool:
    if latest_message is None:
        return False

    previous_at = _normalized_timestamp(latest_message.created_at)
    incoming_at = _normalized_timestamp(payload.received_at)
    if previous_at.astimezone(SESSION_TIMEZONE).date() != incoming_at.astimezone(SESSION_TIMEZONE).date():
        return True
    return incoming_at - previous_at > SESSION_INACTIVITY_LIMIT


def _create_case(session: Session, conversation: Conversation, markers: dict) -> SupportCase:
    support_case = SupportCase(conversation_id=conversation.id)
    if markers and any(markers.get(key) is not None for key in PROBE_MARKER_KEYS):
        _apply_case_markers(support_case, markers)
    session.add(support_case)
    session.flush()
    return support_case


def ensure_conversation(session: Session, *, channel: str, external_chat_id: str, markers: dict | None = None) -> Conversation:
    conversation_external_id = f"{channel}:{external_chat_id}"
    conversation = session.scalar(select(Conversation).where(Conversation.external_id == conversation_external_id))
    if conversation is None:
        conversation = Conversation(external_id=conversation_external_id)
        session.add(conversation)
        session.flush()
    if markers and any(markers.get(key) is not None for key in PROBE_MARKER_KEYS):
        _apply_conversation_markers(conversation, markers)
        session.flush()
    return conversation


def _ensure_entities(session: Session, payload: InboundMessage) -> tuple[User, ChannelAccount, Conversation]:
    user_external_id = f"{payload.channel}:{payload.external_user_id}"
    markers = _extract_session_markers(payload)

    user = session.scalar(select(User).where(User.external_id == user_external_id))
    if user is None:
        user = User(external_id=user_external_id)
        session.add(user)
        session.flush()

    channel_account = session.scalar(
        select(ChannelAccount).where(
            ChannelAccount.channel == payload.channel,
            ChannelAccount.external_user_id == payload.external_user_id,
            ChannelAccount.external_chat_id == payload.external_chat_id,
        )
    )
    if channel_account is None:
        channel_account = ChannelAccount(
            user_id=user.id,
            channel=payload.channel,
            external_user_id=payload.external_user_id,
            external_chat_id=payload.external_chat_id,
        )
        session.add(channel_account)
        session.flush()

    conversation = ensure_conversation(
        session,
        channel=payload.channel,
        external_chat_id=payload.external_chat_id,
        markers=markers,
    )

    return user, channel_account, conversation


def reset_conversation_session(session: Session, payload: InboundMessage) -> dict:
    user, channel_account, conversation = _ensure_entities(session, payload)
    markers = _extract_session_markers(payload)
    open_cases = session.scalars(
        select(SupportCase)
        .where(
            SupportCase.conversation_id == conversation.id,
            SupportCase.status.in_(["open", "waiting_human", "waiting_hermes"]),
        )
        .order_by(SupportCase.id.desc())
    ).all()

    closed_case_ids: list[int] = []
    for support_case in open_cases:
        support_case.status = "resolved"
        support_case.route_mode = "session_reset"
        if markers and any(markers.get(key) is not None for key in PROBE_MARKER_KEYS):
            _apply_case_markers(support_case, markers)
        closed_case_ids.append(support_case.id)

    new_case = SupportCase(conversation_id=conversation.id, status="open", route_mode="session_reset")
    if markers and any(markers.get(key) is not None for key in PROBE_MARKER_KEYS):
        _apply_case_markers(new_case, markers)
    session.add(new_case)
    session.flush()
    return {
        "user_id": user.id,
        "channel_account_id": channel_account.id,
        "conversation_id": conversation.id,
        "case_id": new_case.id,
        "case_status": new_case.status,
        "closed_case_ids": closed_case_ids,
        "closed_case_count": len(closed_case_ids),
        "channel": payload.channel,
        "external_user_id": payload.external_user_id,
        "external_chat_id": payload.external_chat_id,
    }


def resolve_case(session: Session, payload: InboundMessage) -> dict:
    user, channel_account, conversation = _ensure_entities(session, payload)
    markers = _extract_session_markers(payload)

    support_case = session.scalar(
        select(SupportCase)
        .where(SupportCase.conversation_id == conversation.id)
        .order_by(SupportCase.id.desc())
    )
    if support_case is None:
        support_case = _create_case(session, conversation, markers)
    else:
        latest_message = session.scalar(
            select(Message)
            .where(Message.case_id == support_case.id)
            .order_by(Message.created_at.desc(), Message.id.desc())
        )
        if _starts_new_session(latest_message, payload):
            support_case.status = "resolved"
            support_case.route_mode = "session_boundary"
            support_case = _create_case(session, conversation, markers)
        elif markers and any(markers.get(key) is not None for key in PROBE_MARKER_KEYS):
            _apply_case_markers(support_case, markers)
            session.flush()

    return {
        "user_id": user.id,
        "channel_account_id": channel_account.id,
        "conversation_id": conversation.id,
        "case_id": support_case.id,
        "case_status": support_case.status,
        "channel": payload.channel,
        "external_user_id": payload.external_user_id,
        "external_chat_id": payload.external_chat_id,
    }
