from app.services.telegram_gateway import TelegramGatewayService


class RecordingSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def send_message(self, chat_id: str, text: str) -> dict:
        self.calls.append((chat_id, text))
        return {"ok": True, "result": {"chat_id": chat_id, "text": text}}


class StubRouting:
    def handle_inbound(self, payload) -> dict:
        return {
            "case": {"case_status": "waiting_human"},
            "route": {"route": "human_escalation"},
            "outcome": {
                "outcome_type": "human_escalation",
                "outcome_status": "waiting_human",
                "outcome_payload": {"handoff_status": "queued"},
            },
        }

    def reset_session(self, payload) -> dict:
        return {
            "conversation_id": 99,
            "case_id": 100,
            "closed_case_ids": [88],
            "closed_case_count": 1,
        }


def test_telegram_gateway_sends_human_fallback_reply() -> None:
    sender = RecordingSender()
    service = TelegramGatewayService(routing=StubRouting(), sender=sender)

    result = service.handle_update({
        "update_id": 1,
        "message": {
            "message_id": 10,
            "text": "Где мой заказ?",
            "chat": {"id": 12345},
            "from": {"id": 777},
        },
    })

    assert result["ok"] is True
    assert result["delivery"]["sent"] is True
    assert sender.calls == [("12345", "Передал запрос оператору. Скоро вернёмся с ответом.")]
    assert result["reply_text"] == "Передал запрос оператору. Скоро вернёмся с ответом."


def test_telegram_gateway_rejects_chat_not_in_allowlist() -> None:
    sender = RecordingSender()
    service = TelegramGatewayService(routing=StubRouting(), sender=sender, allowed_chat_ids={"115768795"})

    result = service.handle_update({
        "update_id": 2,
        "message": {
            "message_id": 11,
            "text": "Привет",
            "chat": {"id": 99999},
            "from": {"id": 777},
        },
    })

    assert result == {
        "ok": True,
        "ignored": True,
        "reason": "chat_not_allowed",
        "chat_id": "99999",
    }
    assert sender.calls == []


def test_telegram_gateway_handles_new_command() -> None:
    sender = RecordingSender()
    service = TelegramGatewayService(routing=StubRouting(), sender=sender)

    result = service.handle_update({
        "update_id": 3,
        "message": {
            "message_id": 12,
            "text": "/new",
            "chat": {"id": 12345},
            "from": {"id": 777},
        },
    })

    assert result["ok"] is True
    assert result["ignored"] is False
    assert result["reply_text"] == "Сессию сбросил. Начинаем заново — можете отправить новый запрос."
    assert result["app_result"]["command"] == "/new"
    assert result["app_result"]["reset"]["case_id"] == 100
    assert sender.calls == [("12345", "Сессию сбросил. Начинаем заново — можете отправить новый запрос.")]
