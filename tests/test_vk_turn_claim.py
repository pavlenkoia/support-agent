from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.db import Base, make_session_factory
from app.models.conversation import Conversation
from app.models.vk_turn import VkTurn
from app.services.vk_turns import claim_next_due_turn, open_or_extend_turn


def test_only_one_worker_claims_the_same_due_turn(tmp_path):
    factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'claim.db'}")
    Base.metadata.create_all(bind=factory.kw["bind"])
    now = datetime.now(UTC)
    with factory() as session:
        conversation = Conversation(external_id="vk:claim")
        session.add(conversation)
        session.flush()
        turn = open_or_extend_turn(session, conversation_id=conversation.id, event_id=1, now=now, quiet_seconds=0)
        session.commit()

    with factory() as first, factory() as second:
        first_claim = claim_next_due_turn(first, now=now, lease_seconds=60)
        first.commit()
        second_claim = claim_next_due_turn(second, now=now, lease_seconds=60)
        second.commit()
        assert first_claim is not None
        assert first_claim.id == turn.id
        assert second_claim is None

    with factory() as session:
        stored = session.scalar(select(VkTurn).where(VkTurn.id == turn.id))
        assert stored is not None
        assert stored.status == "claimed"
