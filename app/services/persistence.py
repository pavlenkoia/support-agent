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



def persist_outbound_message(session: Session, case_id: int, text: str, *, role: str = "assistant") -> Message:
    message = Message(case_id=case_id, role=role, content=text)
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
