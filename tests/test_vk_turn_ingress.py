from datetime import UTC, datetime, timedelta
from threading import Event, Thread

from sqlalchemy import event, select

from app.core.db import Base, make_session_factory
from app.models.conversation import Conversation
from app.models.outbound_transport_send import OutboundTransportSend
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


def test_new_vk_fragment_during_generation_discards_old_reply_and_combines_unsent_turn(tmp_path):
    factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'superseded.db'}")
    Base.metadata.create_all(bind=factory.kw["bind"])
    started, release = Event(), Event()

    class BlockingRouting(_Routing):
        def handle_inbound(self, payload, *, persist_inbound=True):
            if not self.payloads:
                started.set()
                assert release.wait(timeout=10)
            return super().handle_inbound(payload, persist_inbound=persist_inbound)

    routing, sender = BlockingRouting(), _Sender()
    service = VKGatewayService(routing=routing, sender=sender, session_factory=factory)
    now = datetime.now(UTC)

    def inbound(message_id, text, at):
        service.handle_event({"type": "message_new", "group_id": 55, "object": {"message": {
            "id": message_id, "peer_id": 42, "from_id": 7, "text": text,
            "date": int(at.timestamp()),
        }}})

    inbound(100, "Мне рекомендовали его зовут Максим", now)
    worker = Thread(target=lambda: service.process_due_turns(now=now + timedelta(seconds=6)))
    worker.start()
    try:
        assert started.wait(timeout=10)
        inbound(101, "Но фамилии не знаю", now + timedelta(seconds=11))
    finally:
        release.set()
        worker.join(timeout=10)
    assert not worker.is_alive()
    assert sender.calls == []

    assert service.process_due_turns(now=now + timedelta(seconds=17)) == 1
    assert [payload.text for payload, _ in routing.payloads] == [
        "Мне рекомендовали его зовут Максим",
        "Мне рекомендовали его зовут Максим\nНо фамилии не знаю",
    ]
    assert len(sender.calls) == 1


def test_new_vk_fragment_after_outbound_intent_commit_still_prevents_old_send(tmp_path):
    factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'pending_race.db'}")
    Base.metadata.create_all(bind=factory.kw["bind"])
    routing, sender = _Routing(), _Sender()
    service = VKGatewayService(routing=routing, sender=sender, session_factory=factory)
    now = datetime.now(UTC)

    def inbound(message_id, text, at):
        service.handle_event({"type": "message_new", "group_id": 55, "object": {"message": {
            "id": message_id, "peer_id": 42, "from_id": 7, "text": text,
            "date": int(at.timestamp()),
        }}})

    inbound(100, "Мне рекомендовали Максима", now)
    injected = False

    def before_flush(session, _flush_context, _instances):
        if any(isinstance(item, OutboundTransportSend) and item.send_status == "pending" for item in session.new):
            session.info["pending_intent"] = True

    def after_commit(session):
        nonlocal injected
        if not injected and session.info.pop("pending_intent", False):
            injected = True
            inbound(101, "Фамилии не знаю", now + timedelta(seconds=11))

    event.listen(factory.class_, "before_flush", before_flush)
    event.listen(factory.class_, "after_commit", after_commit)
    try:
        service.process_due_turns(now=now + timedelta(seconds=6))
    finally:
        event.remove(factory.class_, "before_flush", before_flush)
        event.remove(factory.class_, "after_commit", after_commit)
    assert injected
    assert sender.calls == []
    service.process_due_turns(now=now + timedelta(seconds=17))
    assert len(sender.calls) == 1
    assert routing.payloads[-1][0].text == "Мне рекомендовали Максима\nФамилии не знаю"


def test_durable_due_turn_processor_rehydrates_vk_market_attachment_context(tmp_path):
    factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'processor_market.db'}")
    Base.metadata.create_all(bind=factory.kw["bind"])
    routing = _Routing()
    sender = _Sender()
    service = VKGatewayService(routing=routing, sender=sender, session_factory=factory)
    now = datetime.now(UTC)

    accepted = service.handle_event(
        {
            "type": "message_new",
            "group_id": 55,
            "object": {
                "message": {
                    "id": 912,
                    "peer_id": 42,
                    "from_id": 7,
                    "text": "Здравствуйте!\nМеня заинтересовала данная услуга.",
                    "date": int(now.timestamp()),
                    "attachments": [
                        {
                            "type": "market",
                            "market": {
                                "title": "Тандем-прыжок ( с инструктором)",
                                "description": "Высота - 2000-2500 м",
                                "price": {"text": "16 200 ₽"},
                            },
                        }
                    ],
                }
            },
        }
    )
    assert accepted["queued"] is False

    assert service.process_due_turns(now=now + timedelta(seconds=6)) == 1

    assert len(routing.payloads) == 1
    payload, persisted = routing.payloads[0]
    assert persisted is False
    assert "Контекст VK Market-вложения" in payload.text
    assert "Тандем-прыжок ( с инструктором)" in payload.text
    assert "16 200 ₽" in payload.text
