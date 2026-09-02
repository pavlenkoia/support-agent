from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.db import Base, make_session_factory
from app.models.conversation import Conversation
from app.models.vk_turn import VkTurn
from app.services.vk_turns import (
    claim_due_turn,
    open_or_extend_turn,
    suppress_turn,
)


def make_session(tmp_path):
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'turns.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    return session_factory


def conversation(session) -> Conversation:
    item = Conversation(external_id="vk:123")
    session.add(item)
    session.flush()
    return item


def test_open_turn_is_extended_before_claim(tmp_path):
    session_factory = make_session(tmp_path)
    now = datetime.now(UTC)
    with session_factory() as session:
        item = conversation(session)
        first = open_or_extend_turn(session, conversation_id=item.id, event_id=10, now=now, quiet_seconds=5)
        second = open_or_extend_turn(session, conversation_id=item.id, event_id=11, now=now + timedelta(seconds=2), quiet_seconds=5)
        session.commit()
        assert second.id == first.id
        assert second.first_event_id == 10
        assert second.last_event_id == 11
        assert second.status == "open"
        assert second.due_at == now + timedelta(seconds=7)


def test_due_turn_has_one_claim_owner_and_stale_token_cannot_suppress(tmp_path):
    session_factory = make_session(tmp_path)
    now = datetime.now(UTC)
    with session_factory() as session:
        item = conversation(session)
        turn = open_or_extend_turn(session, conversation_id=item.id, event_id=10, now=now, quiet_seconds=0)
        session.commit()

    with session_factory() as session:
        claim = claim_due_turn(session, turn_id=turn.id, now=now, lease_seconds=60)
        session.commit()
        assert claim is not None
        assert claim.status == "claimed"
        assert claim.claim_token

    with session_factory() as session:
        assert claim_due_turn(session, turn_id=turn.id, now=now, lease_seconds=60) is None
        assert suppress_turn(session, turn_id=turn.id, claim_token="wrong", reason="human_override") is False
        assert suppress_turn(session, turn_id=turn.id, claim_token=claim.claim_token, reason="human_override") is True
        session.commit()
        stored = session.scalar(select(VkTurn).where(VkTurn.id == turn.id))
        assert stored is not None
        assert stored.status == "suppressed"
        assert stored.reason == "human_override"


def test_new_inbound_supersedes_a_claimed_turn_before_opening_next_turn(tmp_path):
    session_factory = make_session(tmp_path)
    now = datetime.now(UTC)
    with session_factory() as session:
        item = conversation(session)
        first = open_or_extend_turn(session, conversation_id=item.id, event_id=10, now=now, quiet_seconds=0)
        claim = claim_due_turn(session, turn_id=first.id, now=now, lease_seconds=60)
        assert claim is not None
        next_turn = open_or_extend_turn(
            session,
            conversation_id=item.id,
            event_id=11,
            now=now + timedelta(seconds=1),
            quiet_seconds=5,
        )
        session.commit()

        previous = session.get(VkTurn, first.id)
        assert previous is not None
        assert previous.status == "suppressed"
        assert previous.reason == "newer_inbound"
        assert next_turn.id != first.id
        assert next_turn.status == "open"


def test_expired_claim_can_be_reclaimed_with_new_token(tmp_path):
    session_factory = make_session(tmp_path)
    now = datetime.now(UTC)
    with session_factory() as session:
        item = conversation(session)
        turn = open_or_extend_turn(session, conversation_id=item.id, event_id=10, now=now, quiet_seconds=0)
        session.commit()

    with session_factory() as session:
        first = claim_due_turn(session, turn_id=turn.id, now=now, lease_seconds=1)
        session.commit()

    with session_factory() as session:
        second = claim_due_turn(session, turn_id=turn.id, now=now + timedelta(seconds=2), lease_seconds=60)
        session.commit()
        assert second is not None
        assert second.claim_token != first.claim_token
        assert second.status == "claimed"
