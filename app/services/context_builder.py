from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.message import Message
from app.schemas.message import InboundMessage
from app.workers.summarizer import SummaryService


def build_context(
    session: Session,
    payload: InboundMessage,
    case: dict,
    summary_service: SummaryService | None = None,
) -> dict:
    recent_messages = session.scalars(
        select(Message)
        .where(Message.case_id == case["case_id"])
        .order_by(Message.id.desc())
        .limit(10)
    ).all()

    ordered_messages = list(reversed(recent_messages))
    recent_turns = [message.content for message in ordered_messages]
    recent_messages_payload = [
        {
            "role": message.role,
            "content": message.content,
        }
        for message in ordered_messages
    ]
    summarizer = summary_service or SummaryService()
    session_summary = summarizer.summarize_case(recent_turns)

    return {
        "user_message": payload.text,
        "calendar_reference_at": payload.received_at.isoformat() if payload.received_at is not None else None,
        "recent_turns": recent_turns,
        "recent_messages": recent_messages_payload,
        "session_summary": session_summary,
        "case_state": {
            "case_id": case["case_id"],
            "case_status": case["case_status"],
            "conversation_id": case["conversation_id"],
        },
    }
