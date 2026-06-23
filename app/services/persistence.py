from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.message import Message
from app.models.workflow_event import WorkflowEvent
from app.schemas.message import InboundMessage



def persist_inbound_message(session: Session, case_id: int, payload: InboundMessage) -> Message:
    message = Message(case_id=case_id, role="user", content=payload.text)
    session.add(message)
    session.flush()
    return message



def persist_workflow_event(session: Session, case_id: int, audit: dict) -> WorkflowEvent:
    event = WorkflowEvent(
        case_id=case_id,
        event_type="inbound_processed",
        actor="system:routing",
        payload=audit,
    )
    session.add(event)
    session.flush()
    return event
