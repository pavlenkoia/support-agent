from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.vk_turn import VkTurn


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def open_or_extend_turn(
    session: Session,
    *,
    conversation_id: int,
    event_id: int,
    now: datetime,
    quiet_seconds: int,
) -> VkTurn:
    current = _utc(now)
    turn = session.scalar(
        select(VkTurn)
        .where(VkTurn.conversation_id == conversation_id, VkTurn.status == "open")
        .order_by(VkTurn.id.desc())
        .with_for_update()
    )
    if turn is None:
        turn = VkTurn(
            conversation_id=conversation_id,
            first_event_id=event_id,
            last_event_id=event_id,
            status="open",
            due_at=current + timedelta(seconds=quiet_seconds),
        )
        session.add(turn)
        session.flush()
        return turn
    turn.last_event_id = event_id
    turn.due_at = current + timedelta(seconds=quiet_seconds)
    session.flush()
    return turn


def claim_due_turn(session: Session, *, turn_id: int, now: datetime, lease_seconds: int) -> VkTurn | None:
    current = _utc(now)
    turn = session.scalar(select(VkTurn).where(VkTurn.id == turn_id).with_for_update())
    if turn is None:
        return None
    eligible = (
        (turn.status in {"open", "retry_pending"} and _utc(turn.due_at) <= current)
        or (turn.status == "claimed" and turn.claim_until is not None and _utc(turn.claim_until) <= current)
    )
    if not eligible:
        return None
    turn.status = "claimed"
    turn.claim_token = uuid.uuid4().hex
    turn.claim_until = current + timedelta(seconds=lease_seconds)
    turn.reason = None
    session.flush()
    return turn


def suppress_turn(session: Session, *, turn_id: int, claim_token: str | None, reason: str) -> bool:
    turn = session.scalar(select(VkTurn).where(VkTurn.id == turn_id).with_for_update())
    if turn is None or turn.status in {"sent", "suppressed", "failed"}:
        return False
    if turn.status == "claimed" and turn.claim_token != claim_token:
        return False
    turn.status = "suppressed"
    turn.reason = reason
    turn.claim_token = None
    turn.claim_until = None
    session.flush()
    return True
