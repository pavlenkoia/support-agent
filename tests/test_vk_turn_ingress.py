from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.db import Base, make_session_factory
from app.models.conversation import Conversation
from app.models.transport_event import TransportEvent
from app.models.vk_turn import VkTurn
from app.services.vk_gateway import VKGatewayService
from app.services.vk_turns import open_or_extend_turn


class _Sender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    def send_message(self, *, peer_id: str, text: str, random_id: str | None = None) -> dict:
        self.calls.append((peer_id, text, random_id))
        return {
            "ok": True,
            "sent": True,
            "external_message_id": "9001",
            "sent_at": datetime.now(UTC),
        }


class _Routing:
    def __init__(self) -> None:
        self.payloads = []
        self.outbound = []

    def handle_inbound(self, payload, *, persist_inbound: bool = True) -> dict:
        self.payloads.append((payload, persist_inbound))
        return {
            "case": {"conversation_id": 1, "case_id": 1},
            "outcome": {"outcome_type": "answer", "outcome_payload": {"response_text": "Один ответ"}},
        }

    def record_outbound_message(self, case_id: int, text: str) -> None:
        self.outbound.append((case_id, text))


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


def test_durable_due_turn_processor_combines_persisted_events_and_sends_once(tmp_path):
    factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'processor.db'}")
    Base.metadata.create_all(bind=factory.kw["bind"])
    routing = _Routing()
    sender = _Sender()
    service = VKGatewayService(routing=routing, sender=sender, session_factory=factory)
    now = datetime.now(UTC)

    for event_id, body in ((100, "Первое"), (101, "Второе")):
        accepted = service.handle_event(
            {
                "type": "message_new",
                "group_id": 55,
                "object": {
                    "message": {
                        "id": event_id,
                        "peer_id": 42,
                        "from_id": 7,
                        "text": body,
                        "date": int(now.timestamp()),
                    }
                },
            }
        )
        assert accepted["queued"] is False
        assert accepted["durable_turn_id"]

    processed = service.process_due_turns(now=now + timedelta(seconds=6))

    assert processed == 1
    assert [payload.text for payload, persisted in routing.payloads if not persisted] == ["Первое\nВторое"]
    assert sender.calls == [("42", "Один ответ", sender.calls[0][2])]
    assert routing.outbound == [(1, "Один ответ")]
    with factory() as session:
        turn = session.scalar(select(VkTurn))
        assert turn is not None
        assert turn.status == "sent"
