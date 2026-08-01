from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

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
        typing_interval_seconds: float = 4.0,
    ) -> None:
        self.routing = routing or RoutingService()
        self.sender = sender or TelegramBotClient(token=settings.telegram_bot_token)
        self.allowed_chat_ids = allowed_chat_ids if allowed_chat_ids is not None else parse_csv_set(settings.telegram_allowed_chats)
        self.typing_interval_seconds = typing_interval_seconds

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
        result, typing = self._run_with_typing(chat_id, lambda: self.routing.handle_inbound(inbound))
        reply_text = self._build_reply_text(result)
        delivery = self.sender.send_message(chat_id, reply_text)
        sent = delivery.get("sent")
        if sent is None:
            sent = bool(delivery.get("ok"))
        if sent:
            case_id = ((result.get("case") or {}).get("case_id"))
            if case_id is not None:
                self.routing.record_outbound_message(case_id, reply_text)
        return {
            "ok": True,
            "ignored": False,
            "update_id": update.get("update_id"),
            "reply_text": reply_text,
            "delivery": {
                "sent": bool(sent),
                "result": delivery,
            },
            "typing": typing,
            "app_result": result,
        }

    def _run_with_typing(self, chat_id: str, callback: Callable[[], dict]) -> tuple[dict, dict[str, Any]]:
        typing_result = self.sender.send_chat_action(chat_id, "typing")
        stop_event = threading.Event()
        sent_actions = 1 if typing_result.get("ok") else 0
        errors: list[dict[str, Any]] = []

        def keepalive() -> None:
            nonlocal sent_actions
            while not stop_event.wait(self.typing_interval_seconds):
                result = self.sender.send_chat_action(chat_id, "typing")
                if result.get("ok"):
                    sent_actions += 1
                else:
                    errors.append(result)

        worker = threading.Thread(target=keepalive, name=f"telegram-typing-{chat_id}", daemon=True)
        worker.start()
        try:
            result = callback()
        finally:
            stop_event.set()
            worker.join(timeout=self.typing_interval_seconds + 0.5)

        return result, {
            "ok": bool(typing_result.get("ok")),
            "initial": typing_result,
            "sent_actions": sent_actions,
            "errors": errors,
        }

    @staticmethod
    def _build_reply_text(result: dict) -> str:
        outcome = result.get("outcome", {})
        payload = outcome.get("outcome_payload", {})
        response_text = payload.get("response_text") if isinstance(payload, dict) else None
        if response_text:
            return response_text
        return "Запрос получен."