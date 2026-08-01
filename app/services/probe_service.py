from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models.case import SupportCase
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.workflow_event import WorkflowEvent
from app.schemas.message import InboundMessage
from app.services.case_resolution import reset_conversation_session
from app.services.persistence import persist_workflow_event
from app.services.routing import RoutingService


class ProbeService:
    CHANNEL = "governor_probe"
    SESSION_TYPE = "governor_probe"
    SOURCE = "support_governor"
    ACTOR = "system:support_agent_probe"

    def __init__(
        self,
        *,
        routing: RoutingService | None = None,
        session_factory=SessionLocal,
    ) -> None:
        self.routing = routing or RoutingService(session_factory=session_factory)
        self.session_factory = session_factory

    def start_session(self, *, scenario_name: str | None = None, requested_by: str | None = None) -> dict:
        session_id = f"probe_sess_{uuid4().hex[:12]}"
        payload = self._build_probe_payload(
            session_id=session_id,
            text="__probe_session_start__",
            scenario_name=scenario_name,
            requested_by=requested_by,
        )

        with self.session_factory() as session:
            reset_result = reset_conversation_session(session, payload)
            support_case = self._get_case(session, reset_result["case_id"])
            persist_workflow_event(
                session,
                reset_result["case_id"],
                {
                    "session_id": session_id,
                    "is_test": True,
                    "source": self.SOURCE,
                    "session_type": self.SESSION_TYPE,
                    "scenario_name": scenario_name,
                    "requested_by": requested_by,
                    "channel": "internal_test",
                },
                event_type="probe_session_started",
                actor=self.ACTOR,
            )
            session.commit()
            return {
                "session_id": session_id,
                "case_id": reset_result["case_id"],
                "created_at": support_case.created_at,
                "channel": "internal_test",
                "is_test": True,
                "scenario_name": scenario_name,
                "requested_by": requested_by,
            }

    def send_message(self, session_id: str, text: str) -> dict:
        session_info = self._require_session(session_id)
        payload = self._build_probe_payload(
            session_id=session_id,
            text=text,
            scenario_name=session_info["scenario_name"],
            requested_by=session_info["requested_by"],
        )
        result = self.routing.handle_inbound(payload)
        case_id = int((result.get("case") or {}).get("case_id"))
        reply_text = str(((result.get("outcome") or {}).get("outcome_payload") or {}).get("response_text") or "").strip()
        if reply_text:
            self.routing.record_outbound_message(case_id, reply_text)

        return {
            "session_id": session_id,
            "case_id": case_id,
            "channel": "internal_test",
            "is_test": True,
            "scenario_name": session_info["scenario_name"],
            "requested_by": session_info["requested_by"],
            "final_answer": reply_text,
            "route": result.get("route") or {},
            "outcome": result.get("outcome") or {},
        }

    def list_sessions(
        self,
        *,
        status: str | None = None,
        scenario_name: str | None = None,
        requested_by: str | None = None,
        source: str | None = None,
        session_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        with self.session_factory() as session:
            query = (
                select(SupportCase, Conversation)
                .join(Conversation, Conversation.id == SupportCase.conversation_id)
                .where(
                    Conversation.is_test.is_(True),
                    Conversation.source == (source or self.SOURCE),
                    Conversation.session_type == (session_type or self.SESSION_TYPE),
                )
                .order_by(SupportCase.created_at.desc(), SupportCase.id.desc())
            )
            if status:
                query = query.where(SupportCase.status == status)
            if scenario_name:
                query = query.where(SupportCase.scenario_name == scenario_name)
            if requested_by:
                query = query.where(SupportCase.requested_by == requested_by)

            total = session.execute(
                select(func.count())
                .select_from(SupportCase)
                .join(Conversation, Conversation.id == SupportCase.conversation_id)
                .where(
                    Conversation.is_test.is_(True),
                    Conversation.source == (source or self.SOURCE),
                    Conversation.session_type == (session_type or self.SESSION_TYPE),
                    *( [SupportCase.status == status] if status else [] ),
                    *( [SupportCase.scenario_name == scenario_name] if scenario_name else [] ),
                    *( [SupportCase.requested_by == requested_by] if requested_by else [] ),
                )
            ).scalar_one()

            rows = session.execute(query.limit(limit).offset(offset)).all()
            items = [self._build_session_summary(session, support_case, conversation) for support_case, conversation in rows]
        return {"total": int(total), "items": items}

    def get_session(self, session_id: str) -> dict:
        with self.session_factory() as session:
            conversation = session.scalar(
                select(Conversation).where(Conversation.external_id == self._conversation_external_id(session_id))
            )
            if conversation is None:
                raise LookupError(f"Probe session not found: {session_id}")
            support_case = session.scalar(
                select(SupportCase)
                .where(SupportCase.conversation_id == conversation.id)
                .order_by(SupportCase.id.desc())
            )
            if support_case is None:
                raise LookupError(f"Probe case not found: {session_id}")
            return self._build_session_summary(session, support_case, conversation)

    def get_messages(self, session_id: str) -> dict:
        session_info = self._require_session(session_id)
        with self.session_factory() as session:
            messages = session.scalars(
                select(Message)
                .where(Message.case_id == session_info["case_id"])
                .order_by(Message.created_at.asc(), Message.id.asc())
            ).all()
        return {
            "session_id": session_id,
            "case_id": session_info["case_id"],
            "channel": "internal_test",
            "is_test": True,
            "scenario_name": session_info["scenario_name"],
            "requested_by": session_info["requested_by"],
            "messages": [
                {
                    "id": message.id,
                    "role": message.role,
                    "content": message.content,
                    "created_at": self._normalize_dt(message.created_at),
                }
                for message in messages
            ],
        }

    def wait_reply(self, session_id: str, timeout_sec: int = 30) -> dict:
        _ = timeout_sec
        session_info = self._require_session(session_id)
        with self.session_factory() as session:
            message = session.scalar(
                select(Message)
                .where(
                    Message.case_id == session_info["case_id"],
                    Message.role == "assistant",
                )
                .order_by(Message.created_at.desc(), Message.id.desc())
            )
        if message is None:
            return {
                "session_id": session_id,
                "case_id": session_info["case_id"],
                "channel": "internal_test",
                "is_test": True,
                "scenario_name": session_info["scenario_name"],
                "requested_by": session_info["requested_by"],
                "received": False,
                "final_answer": "",
                "message": None,
                "failure_reason": "no_reply_available",
            }
        return {
            "session_id": session_id,
            "case_id": session_info["case_id"],
            "channel": "internal_test",
            "is_test": True,
            "scenario_name": session_info["scenario_name"],
            "requested_by": session_info["requested_by"],
            "received": True,
            "final_answer": message.content,
            "message": {
                "id": message.id,
                "role": message.role,
                "content": message.content,
                "created_at": self._normalize_dt(message.created_at),
            },
            "failure_reason": None,
        }

    def get_trace(self, session_id: str) -> dict:
        session_info = self._require_session(session_id)
        with self.session_factory() as session:
            events = session.scalars(
                select(WorkflowEvent)
                .where(WorkflowEvent.case_id == session_info["case_id"])
                .order_by(WorkflowEvent.created_at.asc(), WorkflowEvent.id.asc())
            ).all()
        return {
            "session_id": session_id,
            "case_id": session_info["case_id"],
            "channel": "internal_test",
            "is_test": True,
            "scenario_name": session_info["scenario_name"],
            "requested_by": session_info["requested_by"],
            "events": [
                {
                    "id": event.id,
                    "event_type": event.event_type,
                    "actor": event.actor,
                    "payload": event.payload,
                    "created_at": self._normalize_dt(event.created_at),
                }
                for event in events
            ],
        }

    def close_session(self, session_id: str) -> dict:
        with self.session_factory() as session:
            session_info = self._require_session(session_id, session=session)
            support_case = self._get_case(session, session_info["case_id"])
            support_case.status = "resolved"
            support_case.route_mode = self.SESSION_TYPE
            persist_workflow_event(
                session,
                support_case.id,
                {
                    "session_id": session_id,
                    "is_test": True,
                    "source": self.SOURCE,
                    "session_type": self.SESSION_TYPE,
                    "scenario_name": session_info["scenario_name"],
                    "requested_by": session_info["requested_by"],
                    "closed_at": self._normalize_dt(datetime.now(UTC)).isoformat(),
                },
                event_type="probe_session_closed",
                actor=self.ACTOR,
            )
            session.commit()
            return {
                "session_id": session_id,
                "case_id": support_case.id,
                "closed": True,
                "case_status": support_case.status,
                "channel": "internal_test",
                "is_test": True,
                "scenario_name": session_info["scenario_name"],
                "requested_by": session_info["requested_by"],
            }

    def _build_probe_payload(
        self,
        *,
        session_id: str,
        text: str,
        scenario_name: str | None,
        requested_by: str | None,
    ) -> InboundMessage:
        return InboundMessage(
            channel=self.CHANNEL,
            external_user_id=requested_by or self.SOURCE,
            external_chat_id=session_id,
            text=text,
            external_message_id=f"{session_id}:{uuid4().hex[:10]}",
            external_event_type="probe_message",
            external_event_id=f"probe:{session_id}:{uuid4().hex[:10]}",
            received_at=datetime.now(UTC),
            raw_event={
                "session_id": session_id,
                "channel": "internal_test",
                "is_test": True,
            },
            metadata={
                "is_test": True,
                "source": self.SOURCE,
                "session_type": self.SESSION_TYPE,
                "scenario_name": scenario_name,
                "requested_by": requested_by,
                "session_id": session_id,
                "channel": "internal_test",
            },
        )

    def _require_session(self, session_id: str, *, session: Session | None = None) -> dict:
        owns_session = session is None
        session = session or self.session_factory()
        try:
            conversation = session.scalar(
                select(Conversation).where(Conversation.external_id == self._conversation_external_id(session_id))
            )
            if conversation is None:
                raise LookupError(f"Probe session not found: {session_id}")

            support_case = session.scalar(
                select(SupportCase)
                .where(SupportCase.conversation_id == conversation.id)
                .order_by(SupportCase.id.desc())
            )
            if support_case is None:
                raise LookupError(f"Probe case not found: {session_id}")

            start_event = session.scalar(
                select(WorkflowEvent)
                .where(
                    WorkflowEvent.case_id == support_case.id,
                    WorkflowEvent.event_type == "probe_session_started",
                )
                .order_by(WorkflowEvent.id.asc())
            )
            start_payload = start_event.payload if start_event and isinstance(start_event.payload, dict) else {}
            return {
                "conversation_id": conversation.id,
                "case_id": support_case.id,
                "scenario_name": support_case.scenario_name or start_payload.get("scenario_name"),
                "requested_by": support_case.requested_by or conversation.requested_by or start_payload.get("requested_by"),
            }
        finally:
            if owns_session:
                session.close()

    @staticmethod
    def _get_case(session: Session, case_id: int) -> SupportCase:
        support_case = session.scalar(select(SupportCase).where(SupportCase.id == case_id))
        if support_case is None:
            raise LookupError(f"Support case not found: {case_id}")
        return support_case

    def _conversation_external_id(self, session_id: str) -> str:
        return f"{self.CHANNEL}:{session_id}"

    def _build_session_summary(self, session: Session, support_case: SupportCase, conversation: Conversation) -> dict:
        messages = session.scalars(
            select(Message)
            .where(Message.case_id == support_case.id)
            .order_by(Message.created_at.asc(), Message.id.asc())
        ).all()
        last_user_message = next((message.content for message in reversed(messages) if message.role == "user"), None)
        last_assistant_message = next((message.content for message in reversed(messages) if message.role == "assistant"), None)
        last_activity = messages[-1].created_at if messages else support_case.created_at
        external_id = conversation.external_id or ""
        session_id = external_id.split(":", 1)[1] if ":" in external_id else external_id
        return {
            "session_id": session_id,
            "conversation_id": conversation.id,
            "case_id": support_case.id,
            "case_status": support_case.status,
            "route_mode": support_case.route_mode,
            "channel": "internal_test",
            "is_test": bool(conversation.is_test),
            "source": support_case.source or conversation.source,
            "session_type": support_case.session_type or conversation.session_type,
            "scenario_name": support_case.scenario_name or conversation.scenario_name,
            "requested_by": support_case.requested_by or conversation.requested_by,
            "message_count": len(messages),
            "last_user_message": last_user_message,
            "last_assistant_message": last_assistant_message,
            "last_activity_at": self._normalize_dt(last_activity),
            "created_at": self._normalize_dt(support_case.created_at),
        }

    @staticmethod
    def _normalize_dt(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
