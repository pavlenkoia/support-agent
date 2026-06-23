from __future__ import annotations

from app.core.config import parse_csv_set, settings
from app.integrations.telegram.client import TelegramBotClient
from app.schemas.message import InboundMessage
from app.services.routing import RoutingService


class TelegramGatewayService:
    def __init__(
        self,
        routing: RoutingService | None = None,
        sender: TelegramBotClient | None = None,
        allowed_chat_ids: set[str] | None = None,
    ) -> None:
        self.routing = routing or RoutingService()
        self.sender = sender or TelegramBotClient(token=settings.telegram_bot_token)
        self.allowed_chat_ids = allowed_chat_ids if allowed_chat_ids is not None else parse_csv_set(settings.telegram_allowed_chats)

    def handle_update(self, update: dict) -> dict:
        message = update.get("message") or update.get("edited_message") or {}
        text = (message.get("text") or "").strip()
        chat_id = str((message.get("chat") or {}).get("id", ""))
        user_id = str((message.get("from") or {}).get("id", ""))

        if not text or not chat_id or not user_id:
            return {
                "ok": True,
                "ignored": True,
                "reason": "unsupported_update",
            }

        if self.allowed_chat_ids and chat_id not in self.allowed_chat_ids:
            return {
                "ok": True,
                "ignored": True,
                "reason": "chat_not_allowed",
                "chat_id": chat_id,
            }

        if text == "/new":
            inbound = InboundMessage(
                channel="telegram",
                external_user_id=user_id,
                external_chat_id=chat_id,
                text=text,
            )
            reset_result = self.routing.reset_session(inbound)
            reply_text = "Сессию сбросил. Начинаем заново — можете отправить новый запрос."
            delivery = self.sender.send_message(chat_id, reply_text)
            sent = delivery.get("sent")
            if sent is None:
                sent = bool(delivery.get("ok"))
            return {
                "ok": True,
                "ignored": False,
                "update_id": update.get("update_id"),
                "reply_text": reply_text,
                "delivery": {
                    "sent": bool(sent),
                    "result": delivery,
                },
                "app_result": {
                    "command": "/new",
                    "reset": reset_result,
                },
            }

        inbound = InboundMessage(
            channel="telegram",
            external_user_id=user_id,
            external_chat_id=chat_id,
            text=text,
        )
        result = self.routing.handle_inbound(inbound)
        reply_text = self._build_reply_text(result)
        delivery = self.sender.send_message(chat_id, reply_text)
        sent = delivery.get("sent")
        if sent is None:
            sent = bool(delivery.get("ok"))
        return {
            "ok": True,
            "ignored": False,
            "update_id": update.get("update_id"),
            "reply_text": reply_text,
            "delivery": {
                "sent": bool(sent),
                "result": delivery,
            },
            "app_result": result,
        }

    @staticmethod
    def _build_reply_text(result: dict) -> str:
        outcome = result.get("outcome", {})
        outcome_type = outcome.get("outcome_type")
        payload = outcome.get("outcome_payload", {})

        if outcome_type == "direct_answer":
            return payload.get("response_text") or "Ответ подготовлен."

        if outcome_type == "human_escalation":
            return "Передал запрос оператору. Скоро вернёмся с ответом."

        if outcome_type == "hermes_escalation":
            draft = payload.get("response_draft") if isinstance(payload, dict) else None
            return draft or "Запрос принят в обработку. Скоро вернёмся с ответом."

        return "Запрос получен. Скоро вернёмся с ответом."
