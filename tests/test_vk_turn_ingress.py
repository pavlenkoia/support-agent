from datetime import UTC, datetime

from sqlalchemy import select

from app.core.db import Base, make_session_factory
from app.models.conversation import Conversation
from app.models.transport_event import TransportEvent
from app.models.vk_turn import VkTurn
from app.services.vk_turns import open_or_extend_turn


def test_vk_ingress_creates_one_durable_open_turn_for_two_events(tmp_path):
    factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'ingress.db'}")
    Base.metadata.create_all(bind=factory.kw["bind"])
    now = datetime.now(UTC)
    with factory() as session:
        conversation = Conversation(external_id="vk:42")
        session.add(conversation)
        session.flush()
        for event_id in (1, 2):
            event = TransportEvent(
                platform="vk", event_type="message_new", dedupe_key=f"event:{event_id}",
                payload_json={}, conversation_external_id="vk:42", status="received", received_at=now,
            )
            session.add(event)
            session.flush()
            open_or_extend_turn(session, conversation_id=conversation.id, event_id=event.id, now=now, quiet_seconds=5)
        session.commit()
        turns = list(session.scalars(select(VkTurn).order_by(VkTurn.id)))
        assert len(turns) == 1
        assert turns[0].first_event_id != turns[0].last_event_id
        assert turns[0].status == "open"
