from datetime import UTC, datetime

from sqlalchemy import select

from app.core.db import Base, make_session_factory
from app.models.outbound_transport_send import OutboundTransportSend
from app.services.persistence import (
    find_recent_outbound_transport_match,
    persist_outbound_transport_send,
    persist_transport_event,
)


def test_transport_event_dedupe_returns_existing_record(tmp_path) -> None:
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'persistence.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])

    with session_factory() as session:
        first, created_first = persist_transport_event(
            session,
            platform="vk",
            event_type="message_new",
            dedupe_key="vk:message_new:1:2",
            payload_json={"foo": "bar"},
        )
        second, created_second = persist_transport_event(
            session,
            platform="vk",
            event_type="message_new",
            dedupe_key="vk:message_new:1:2",
            payload_json={"foo": "bar"},
        )
        session.commit()

    assert created_first is True
    assert created_second is False
    assert first.id == second.id


def test_find_recent_outbound_transport_match_falls_back_to_hash_and_time(tmp_path) -> None:
    session_factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'persistence2.db'}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])
    sent_at = datetime(2026, 6, 26, 12, 0, tzinfo=UTC)

    with session_factory() as session:
        persist_outbound_transport_send(
            session,
            platform="vk",
            peer_external_id="2001",
            random_id="rid-1",
            content_text="Готовый ответ",
            external_message_id=None,
            sent_at=sent_at,
        )
        session.commit()

    with session_factory() as session:
        matched = find_recent_outbound_transport_match(
            session,
            platform="vk",
            peer_external_id="2001",
            external_message_id=None,
            content_text="Готовый ответ",
            event_time=sent_at,
        )
        assert matched is not None
        assert matched.random_id == "rid-1"
        assert session.scalar(select(OutboundTransportSend).where(OutboundTransportSend.random_id == "rid-1")) is not None
