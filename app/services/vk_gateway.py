from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.integrations.vk.sender import VKSender
from app.models.conversation import Conversation
from app.schemas.message import InboundMessage
from app.services.case_resolution import ensure_conversation, resolve_case
from app.services.persistence import (
    activate_human_override,
    find_recent_outbound_transport_match,
    find_transport_event,
    get_or_create_conversation_transport_state,
    is_override_active,
    mark_transport_event_failed,
    mark_transport_event_processed,
    persist_inbound_message,
    persist_outbound_transport_send,
    persist_transport_event,
    reconcile_outbound_transport_send,
    set_last_bot_reply,
    set_last_inbound_message,
)
from app.services.routing import RoutingService


class VKGatewayService:
    def __init__(
        self,
        *,
        routing: RoutingService | None = None,
        sender: VKSender | None = None,
        session_factory=SessionLocal,
        override_silence_seconds: int | None = None,
    ) -> None:
        self.routing = routing or RoutingService()
        self.sender = sender or VKSender()
        self.session_factory = session_factory
        self.override_silence_seconds = override_silence_seconds or settings.vk_override_silence_seconds

    def handle_event(self, event: dict[str, Any]) -> dict[str, Any]:
        event_type = str(event.get("type") or "")
        if event_type == "message_new":
            return self._handle_message_new(event)
        if event_type == "message_reply":
            return self._handle_message_reply(event)
        return {"ok": True, "ignored": True, "reason": "unsupported_event_type", "event_type": event_type}

    def _handle_message_new(self, event: dict[str, Any]) -> dict[str, Any]:
        message = self._extract_message(event)
        text = str(message.get("text") or "").strip()
        peer_id = self._string_id(message.get("peer_id"))
        from_id = self._string_id(message.get("from_id"))
        message_id = self._string_id(message.get("id"))
        if not text or not peer_id or not from_id or not message_id:
            return {"ok": True, "ignored": True, "reason": "unsupported_message_new"}

        event_time = self._event_time(message)
        dedupe_key = f"vk:message_new:{peer_id}:{message_id}"
        inbound = InboundMessage(
            channel="vk",
            external_user_id=from_id,
            external_chat_id=peer_id,
            text=text,
            external_message_id=message_id,
            external_event_type="message_new",
            external_event_id=dedupe_key,
            received_at=event_time,
            raw_event=event,
            metadata={
                "group_id": event.get("group_id"),
                "peer_id": peer_id,
                "from_id": from_id,
            },
        )

        with self.session_factory() as session:
            transport_event, created = persist_transport_event(
                session,
                platform="vk",
                event_type="message_new",
                dedupe_key=dedupe_key,
                payload_json=event,
                external_event_id=dedupe_key,
                external_message_id=message_id,
                conversation_external_id=self._conversation_external_id(peer_id),
                received_at=event_time,
            )
            if not created:
                session.commit()
                return {"ok": True, "ignored": True, "reason": "duplicate_event", "event_type": "message_new"}

            conversation = ensure_conversation(session, channel="vk", external_chat_id=peer_id)
            state = get_or_create_conversation_transport_state(session, conversation_id=conversation.id, platform="vk")
            set_last_inbound_message(session, state, external_message_id=message_id)

            if is_override_active(state, now=event_time):
                case = resolve_case(session, inbound)
                persist_inbound_message(session, case["case_id"], inbound)
                mark_transport_event_processed(session, transport_event, status="suppressed")
                session.commit()
                return {
                    "ok": True,
                    "ignored": False,
                    "suppressed": True,
                    "reason": "human_override_active",
                    "case": case,
                    "event_type": "message_new",
                }

            session.commit()

        try:
            result = self.routing.handle_inbound(inbound)
        except Exception as exc:
            with self.session_factory() as session:
                stored_event = find_transport_event(session, dedupe_key)
                if stored_event is not None:
                    mark_transport_event_failed(session, stored_event, str(exc))
                session.commit()
            raise

        reply_text = self._build_reply_text(result)
        if not reply_text:
            with self.session_factory() as session:
                stored_event = find_transport_event(session, dedupe_key)
                if stored_event is not None:
                    mark_transport_event_processed(session, stored_event, status="processed")
                session.commit()
            return {"ok": True, "ignored": False, "reply_skipped": True, "app_result": result}

        case = result.get("case") or {}
        conversation_id = case.get("conversation_id")
        case_id = case.get("case_id")

        with self.session_factory() as session:
            if conversation_id is None:
                conversation = session.scalar(select(Conversation).where(Conversation.external_id == self._conversation_external_id(peer_id)))
                if conversation is not None:
                    conversation_id = conversation.id
            if conversation_id is None:
                conversation = ensure_conversation(session, channel="vk", external_chat_id=peer_id)
                conversation_id = conversation.id
            state = get_or_create_conversation_transport_state(session, conversation_id=conversation_id, platform="vk")
            if is_override_active(state, now=datetime.now(UTC)):
                stored_event = find_transport_event(session, dedupe_key)
                if stored_event is not None:
                    mark_transport_event_processed(session, stored_event, status="suppressed")
                session.commit()
                return {
                    "ok": True,
                    "ignored": False,
                    "suppressed": True,
                    "reason": "human_override_activated_before_send",
                    "app_result": result,
                }
            session.commit()

        delivery = self.sender.send_message(peer_id=peer_id, text=reply_text)
        sent = delivery.get("sent")
        if sent is None:
            sent = bool(delivery.get("ok"))

        with self.session_factory() as session:
            stored_event = find_transport_event(session, dedupe_key)
            if stored_event is not None:
                mark_transport_event_processed(session, stored_event, status="processed" if sent else "failed")
                if not sent:
                    stored_event.error_text = str(delivery.get("reason") or delivery.get("vk_response") or "vk_send_failed")
            if sent:
                state = get_or_create_conversation_transport_state(session, conversation_id=conversation_id, platform="vk")
                persist_outbound_transport_send(
                    session,
                    platform="vk",
                    conversation_id=conversation_id,
                    case_id=case_id,
                    peer_external_id=peer_id,
                    random_id=str(delivery["random_id"]),
                    content_text=reply_text,
                    external_message_id=delivery.get("external_message_id"),
                    sent_at=delivery.get("sent_at"),
                )
                set_last_bot_reply(session, state, replied_at=delivery.get("sent_at"))
            session.commit()

        if sent and case_id is not None:
            self.routing.record_outbound_message(case_id, reply_text)

        return {
            "ok": True,
            "ignored": False,
            "event_type": "message_new",
            "reply_text": reply_text,
            "delivery": delivery,
            "app_result": result,
            "suppressed": False,
        }

    def _handle_message_reply(self, event: dict[str, Any]) -> dict[str, Any]:
        message = self._extract_message(event)
        peer_id = self._string_id(message.get("peer_id"))
        message_id = self._string_id(message.get("id"))
        text = str(message.get("text") or "").strip()
        if not peer_id or not message_id:
            return {"ok": True, "ignored": True, "reason": "unsupported_message_reply"}

        event_time = self._event_time(message)
        dedupe_key = f"vk:message_reply:{peer_id}:{message_id}"

        with self.session_factory() as session:
            transport_event, created = persist_transport_event(
                session,
                platform="vk",
                event_type="message_reply",
                dedupe_key=dedupe_key,
                payload_json=event,
                external_event_id=dedupe_key,
                external_message_id=message_id,
                conversation_external_id=self._conversation_external_id(peer_id),
                received_at=event_time,
            )
            if not created:
                session.commit()
                return {"ok": True, "ignored": True, "reason": "duplicate_event", "event_type": "message_reply"}

            conversation = ensure_conversation(session, channel="vk", external_chat_id=peer_id)
            state = get_or_create_conversation_transport_state(session, conversation_id=conversation.id, platform="vk")
            matched = find_recent_outbound_transport_match(
                session,
                platform="vk",
                peer_external_id=peer_id,
                external_message_id=message_id,
                content_text=text,
                event_time=event_time,
            )
            if matched is not None:
                reconcile_outbound_transport_send(session, matched, external_message_id=message_id, reconciled_at=event_time)
                mark_transport_event_processed(session, transport_event)
                session.commit()
                return {
                    "ok": True,
                    "ignored": False,
                    "event_type": "message_reply",
                    "sent_by": "bot",
                    "matched_send_id": matched.id,
                }

            activate_human_override(
                session,
                state,
                admin_replied_at=event_time,
                silence_seconds=self.override_silence_seconds,
            )
            mark_transport_event_processed(session, transport_event)
            session.commit()
            return {
                "ok": True,
                "ignored": False,
                "event_type": "message_reply",
                "sent_by": "admin",
                "override_until": state.human_override_until.isoformat() if state.human_override_until else None,
            }

    @staticmethod
    def _extract_message(event: dict[str, Any]) -> dict[str, Any]:
        obj = event.get("object") or {}
        message = obj.get("message") if isinstance(obj, dict) else None
        if isinstance(message, dict):
            return message
        return obj if isinstance(obj, dict) else {}

    @staticmethod
    def _string_id(value: Any) -> str:
        if value is None:
            return ""
        return str(value)

    @staticmethod
    def _event_time(message: dict[str, Any]) -> datetime:
        raw = message.get("date")
        if raw is None:
            return datetime.now(UTC)
        return datetime.fromtimestamp(int(raw), tz=UTC)

    @staticmethod
    def _build_reply_text(result: dict[str, Any]) -> str:
        outcome = result.get("outcome") or {}
        payload = outcome.get("outcome_payload") or {}
        return str(payload.get("response_text") or "").strip()

    @staticmethod
    def _conversation_external_id(peer_id: str) -> str:
        return f"vk:{peer_id}"
