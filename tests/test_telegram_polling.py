from app.services.telegram_gateway import TelegramGatewayService
from app.workers.main import process_once


class RecordingPoller:
    def __init__(self, updates):
        self.updates = updates
        self.offsets = []

    def get_updates(self, offset=None, timeout=30):
        self.offsets.append(offset)
        return {"ok": True, "result": self.updates}


class RecordingAcker:
    def __init__(self):
        self.offsets = []

    def ack(self, offset):
        self.offsets.append(offset)


class RecordingSender:
    def __init__(self):
        self.calls = []

    def send_chat_action(self, chat_id: str, action: str = "typing") -> dict:
        self.calls.append(("action", chat_id, action))
        return {"ok": True, "sent": True, "action": action}

    def send_message(self, chat_id: str, text: str) -> dict:
        self.calls.append(("message", chat_id, text))
        return {"ok": True, "sent": True}


class StubRouting:
    def handle_inbound(self, payload) -> dict:
        return {
            "case": {"case_status": "resolved", "case_id": 1},
            "route": {"route": "cannot_answer"},
            "outcome": {
                "outcome_type": "cannot_answer",
                "outcome_status": "completed",
                "outcome_payload": {"response_text": "Сейчас не могу дать точный ответ на этот вопрос."},
            },
        }

    def record_outbound_message(self, case_id: int, text: str) -> None:
        self.recorded = (case_id, text)

    def reset_session(self, payload) -> dict:
        return {
            "conversation_id": 99,
            "case_id": 100,
            "closed_case_ids": [88],
            "closed_case_count": 1,
        }


def test_process_once_polls_update_routes_and_acks_offset() -> None:
    sender = RecordingSender()
    gateway = TelegramGatewayService(routing=StubRouting(), sender=sender)
    poller = RecordingPoller([
        {
            "update_id": 101,
            "message": {
                "message_id": 10,
                "text": "Где мой заказ?",
                "chat": {"id": 12345},
                "from": {"id": 777},
            },
        }
    ])
    acker = RecordingAcker()

    result = process_once(gateway=gateway, poller=poller, acker=acker, offset=100)

    assert result["next_offset"] == 102
    assert sender.calls == [
        ("action", "12345", "typing"),
        ("message", "12345", "Сейчас не могу дать точный ответ на этот вопрос."),
    ]
    assert acker.offsets == [102]
    assert poller.offsets == [100]


def test_process_once_no_updates_keeps_offset() -> None:
    gateway = TelegramGatewayService(routing=StubRouting(), sender=RecordingSender())
    poller = RecordingPoller([])
    acker = RecordingAcker()

    result = process_once(gateway=gateway, poller=poller, acker=acker, offset=55)

    assert result["next_offset"] == 55
    assert acker.offsets == []
