from datetime import UTC, datetime, timedelta

import pytest
from test_viewer_api import make_viewer_service

from app.models.message import Message
from app.models.outbound_transport_send import OutboundTransportSend
from app.models.transport_event import TransportEvent


@pytest.mark.parametrize("reply_id, followup_id", [("35171", "35172"), ("35172", "35171")])
def test_viewer_uses_vk_order_when_reply_and_followup_share_a_second(tmp_path, reply_id, followup_id):
    service = make_viewer_service(tmp_path)
    t = datetime(2026, 6, 30, 14, 6, 22, tzinfo=UTC)
    with service.session_factory() as session:
        # Followup can commit before the assistant record: database IDs are not VK order.
        followup = Message(case_id=1, role="user", content="Я не разу не прыгала 😃", created_at=t)
        reply = Message(case_id=1, role="assistant", content="Ответ о записи", created_at=t + timedelta(microseconds=589682))
        session.add_all([followup, reply])
        session.flush()
        expected = ([str(reply.id), str(followup.id)] if int(reply_id) < int(followup_id)
                    else [str(followup.id), str(reply.id)])
        session.add_all([
            TransportEvent(platform="vk", event_type="message_reply", external_message_id=reply_id, conversation_external_id="vk:123", dedupe_key="reply", payload_json={"object": {"text": reply.content, "date": int(t.timestamp())}}, received_at=t),
            TransportEvent(platform="vk", event_type="message_new", external_message_id=followup_id, conversation_external_id="vk:123", dedupe_key="followup", payload_json={"object": {"message": {"text": followup.content, "date": int(t.timestamp())}}}, received_at=t),
            OutboundTransportSend(platform="vk", conversation_id=1, case_id=1, peer_external_id="123", random_id="1", external_message_id=reply_id, content_hash="test", content_text=reply.content, send_status="reconciled", sent_at=t + timedelta(microseconds=585630)),
        ])
        session.commit()
    result = service.get_dialog_messages_for_day("vk:123", t.date())
    selected = [m for m in result.messages if m.id in expected]
    assert [m.id for m in selected] == expected
    assert [m.sent_at for m in selected] == [t, t]


def test_viewer_keeps_unmatched_history_and_does_not_rewrite_storage(tmp_path):
    service = make_viewer_service(tmp_path)
    result = service.get_dialog_messages_for_day("vk:123", datetime(2026, 6, 30).date())
    assert [m.id for m in result.messages] == ["3", "1", "2"]
    with service.session_factory() as session:
        assert session.get(Message, 2).created_at == datetime(2026, 6, 30, 9, 16)


@pytest.mark.parametrize("defect", ["missing_echo", "wrong_text", "duplicate_send", "duplicate_message", "invalid_date", "invalid_id"])
def test_ambiguous_or_missing_transport_keeps_stored_time(defect):
    from app.services.viewer_timeline import vk_timeline

    t = datetime(2026, 6, 30, 14, 6, 22, tzinfo=UTC)
    stored = t + timedelta(microseconds=589682)
    message = Message(id=1, case_id=1, role="assistant", content="Ответ", created_at=stored)
    send = OutboundTransportSend(case_id=1, content_text="Ответ", external_message_id="10", send_status="sent")
    event = TransportEvent(event_type="message_reply", external_message_id="10", payload_json={"object": {"text": "Ответ", "date": int(t.timestamp())}})
    events, sends, messages = [event], [send], [message]
    if defect == "missing_echo":
        events = []
    elif defect == "wrong_text":
        event.payload_json["object"]["text"] = "Другой ответ"
    elif defect == "duplicate_send":
        sends = [send, send]
    elif defect == "duplicate_message":
        messages.append(Message(id=2, case_id=1, role="assistant", content="Ответ", created_at=stored))
    elif defect == "invalid_date":
        event.payload_json["object"]["date"] = "invalid"
    else:
        event.external_message_id = "invalid"
    result = vk_timeline(messages, events, sends)
    assert all(at == stored for _, at in result)


def test_operator_and_customer_use_numeric_vk_order_not_ingestion_order():
    from app.services.viewer_timeline import vk_timeline

    t = datetime(2026, 6, 30, 14, 6, 22, tzinfo=UTC)
    customer = Message(id=1, case_id=1, role="user", content="Уточнение", created_at=t)
    human = Message(id=2, case_id=1, role="human", content="Ответ оператора", created_at=t)
    events = [
        TransportEvent(event_type="message_new", external_message_id="10", payload_json={"object": {"message": {"text": customer.content, "date": int(t.timestamp())}}}),
        TransportEvent(event_type="message_reply", external_message_id="9", payload_json={"object": {"text": human.content, "date": int(t.timestamp())}}),
    ]
    assert [m.id for m, _ in vk_timeline([customer, human], events, [])] == [2, 1]
