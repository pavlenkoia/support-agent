from __future__ import annotations

from app.core.config import parse_csv_set, settings
from app.core.db import SessionLocal
from app.integrations.telegram.client import TelegramBotClient
from app.schemas.message import InboundMessage
from app.services.inbound_queue import Generation, InboundQueue
from app.services.persistence import (
    find_transport_event,
    mark_transport_event_processed,
    persist_transport_event,
)
from app.services.routing import RoutingService


class TelegramGatewayService:
    def __init__(
        self,
        routing: RoutingService | None = None,
        sender: TelegramBotClient | None = None,
        allowed_chat_ids: set[str] | None = None,
        typing_interval_seconds: float = 4.0,
        session_factory=None,
        queue: InboundQueue | None = None,
    ) -> None:
        self.routing = routing or RoutingService()
        self.sender = sender or TelegramBotClient(token=settings.telegram_bot_token)
        self.allowed_chat_ids = allowed_chat_ids if allowed_chat_ids is not None else parse_csv_set(settings.telegram_allowed_chats)
        self.typing_interval_seconds = typing_interval_seconds
        self.session_factory = session_factory or getattr(self.routing, "session_factory", None) or SessionLocal
        self.queue = queue or InboundQueue(
            self._process_generation,
            quiet_seconds=settings.inbound_coalesce_quiet_seconds,
            max_wait_seconds=settings.inbound_coalesce_max_wait_seconds,
            autostart=True,
        )

    def handle_update(self, update: dict) -> dict:
        message = update.get("message") or update.get("edited_message") or {}
        text = (message.get("text") or "").strip()
        chat_id = str((message.get("chat") or {}).get("id", ""))
        user_id = str((message.get("from") or {}).get("id", ""))
        message_id = str(message.get("message_id") or "")
        if not text or not chat_id or not user_id or not message_id:
            return {"ok": True, "ignored": True, "reason": "unsupported_update"}
        if self.allowed_chat_ids and chat_id not in self.allowed_chat_ids:
            return {"ok": True, "ignored": True, "reason": "chat_not_allowed", "chat_id": chat_id}

        inbound = InboundMessage(
            channel="telegram",
            external_user_id=user_id,
            external_chat_id=chat_id,
            text=text,
            external_message_id=message_id,
            external_event_type="message",
            external_event_id=f"telegram:message:{chat_id}:{message_id}",
            raw_event=update,
        )
        if text == "/new":
            self.queue.cancel("telegram", chat_id)
            reset_result = self.routing.reset_session(inbound)
            reply_text = "Сессию сбросил. Начинаем заново — можете отправить новый запрос."
            delivery = self.sender.send_message(chat_id, reply_text)
            return {"ok": True, "ignored": False, "update_id": update.get("update_id"), "reply_text": reply_text, "delivery": delivery, "app_result": {"command": "/new", "reset": reset_result}}

        if not self._persist_raw_event(inbound):
            return {"ok": True, "ignored": True, "reason": "duplicate_event"}
        batch = self.queue.submit(inbound)
        return {"ok": True, "ignored": False, "queued": True, "update_id": update.get("update_id"), "batch_id": batch.batch_id, "revision": batch.revision}

    def flush_due(self) -> int:
        return self.queue.flush_due()

    def _process_generation(self, inbound: InboundMessage, generation: Generation) -> str | None:
        result = self.routing.handle_inbound(inbound, persist_inbound=False)
        reply_text = self._build_reply_text(result)
        if not reply_text:
            return "retry_pending"
        case_id = (result.get("case") or {}).get("case_id")
        if case_id is None:
            return "retry_pending"

        def persist_and_deliver() -> str:
            self.routing.persist_inbound_message(case_id, inbound)
            delivery = self.sender.send_message(inbound.external_chat_id, reply_text)
            if not delivery.get("sent", delivery.get("ok")):
                return "retry_pending"
            self.routing.record_outbound_message(case_id, reply_text)
            self._mark_sources_processed(generation)
            return "delivered"

        return generation.run_if_current(persist_and_deliver)

    def _persist_raw_event(self, inbound: InboundMessage) -> bool:
        if not hasattr(self.routing, "session_factory"):
            return True
        with self.session_factory() as session:
            _, created = persist_transport_event(
                session,
                platform="telegram",
                event_type="message",
                dedupe_key=str(inbound.external_event_id),
                payload_json=inbound.raw_event or {},
                external_event_id=inbound.external_event_id,
                external_message_id=inbound.external_message_id,
                conversation_external_id=f"telegram:{inbound.external_chat_id}",
                received_at=inbound.received_at,
            )
            session.commit()
            return created

    def _mark_sources_processed(self, generation: Generation) -> None:
        if not hasattr(self.routing, "session_factory"):
            return
        with self.session_factory() as session:
            for source in generation.source_messages:
                if source.external_event_id and (event := find_transport_event(session, source.external_event_id)) is not None:
                    mark_transport_event_processed(session, event)
            session.commit()

    @staticmethod
    def _build_reply_text(result: dict) -> str:
        outcome = result.get("outcome", {})
        payload = outcome.get("outcome_payload", {})
        response_text = payload.get("response_text") if isinstance(payload, dict) else None
        return str(response_text or "").strip()
