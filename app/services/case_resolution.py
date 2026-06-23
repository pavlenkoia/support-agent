from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.case import SupportCase
from app.models.channel import ChannelAccount
from app.models.conversation import Conversation
from app.models.user import User
from app.schemas.message import InboundMessage


def _ensure_entities(session: Session, payload: InboundMessage) -> tuple[User, ChannelAccount, Conversation]:
    user_external_id = f"{payload.channel}:{payload.external_user_id}"
    conversation_external_id = f"{payload.channel}:{payload.external_chat_id}"

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

    conversation = session.scalar(select(Conversation).where(Conversation.external_id == conversation_external_id))
    if conversation is None:
        conversation = Conversation(external_id=conversation_external_id)
        session.add(conversation)
        session.flush()

    return user, channel_account, conversation


def reset_conversation_session(session: Session, payload: InboundMessage) -> dict:
    user, channel_account, conversation = _ensure_entities(session, payload)
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
        closed_case_ids.append(support_case.id)

    new_case = SupportCase(conversation_id=conversation.id, status="open", route_mode="session_reset")
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

    support_case = session.scalar(
        select(SupportCase)
        .where(
            SupportCase.conversation_id == conversation.id,
            SupportCase.status.in_(["open", "waiting_human", "waiting_hermes"]),
        )
        .order_by(SupportCase.id.desc())
    )
    if support_case is None:
        support_case = SupportCase(conversation_id=conversation.id)
        session.add(support_case)
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
