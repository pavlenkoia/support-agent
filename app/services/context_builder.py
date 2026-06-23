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

    recent_turns = [message.content for message in reversed(recent_messages)]
    summarizer = summary_service or SummaryService()
    session_summary = summarizer.summarize_case(recent_turns)

    return {
        "user_message": payload.text,
        "recent_turns": recent_turns,
        "session_summary": session_summary,
        "case_state": {
            "case_id": case["case_id"],
            "case_status": case["case_status"],
            "conversation_id": case["conversation_id"],
        },
    }
