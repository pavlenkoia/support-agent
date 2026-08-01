from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.case import SupportCase
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.schemas.viewer import (
    ViewerDialogItem,
    ViewerDialogMessagesResponse,
    ViewerMessageItem,
)


@dataclass(slots=True)
class _ViewerConversationMeta:
    conversation_id: str
    case_id: int | None
    display_name: str | None
    external_chat_id: str


class ViewerService:
    def __init__(
        self,
        session_factory: sessionmaker = SessionLocal,
        viewer_timezone: str = settings.viewer_timezone,
    ) -> None:
        self.session_factory = session_factory
        self.viewer_timezone = ZoneInfo(viewer_timezone)

    def list_dialogs_for_day(self, day: date) -> list[ViewerDialogItem]:
        day_start, day_end = self._day_bounds(day)
        with self.session_factory() as session:
            display_name_expr = func.max(User.display_name)
            stmt = (
                select(
                    Conversation.external_id.label("conversation_id"),
                    func.max(SupportCase.id).label("case_id"),
                    display_name_expr.label("display_name"),
                    func.max(Message.created_at).label("last_message_at"),
                    func.count(Message.id).label("message_count"),
                )
                .join(SupportCase, SupportCase.conversation_id == Conversation.id)
                .join(Message, Message.case_id == SupportCase.id)
                .outerjoin(User, User.external_id == Conversation.external_id)
                .where(
                    Conversation.external_id.like("vk:%"),
                    Message.created_at >= day_start,
                    Message.created_at < day_end,
                )
                .group_by(Conversation.external_id)
                .order_by(func.max(Message.created_at).asc())
            )
            rows = session.execute(stmt).all()

        return [
            ViewerDialogItem(
                conversation_id=row.conversation_id,
                case_id=int(row.case_id) if row.case_id is not None else None,
                display_name=row.display_name,
                external_chat_id=self._external_chat_id_from_conversation(row.conversation_id),
                last_message_at=self._normalize_dt(row.last_message_at),
                message_count=int(row.message_count),
            )
            for row in rows
        ]

    def get_dialog_messages_for_day(self, conversation_id: str, day: date) -> ViewerDialogMessagesResponse:
        day_start, day_end = self._day_bounds(day)
        with self.session_factory() as session:
            meta = self._load_conversation_meta(session, conversation_id)
            stmt = (
                select(Message)
                .join(SupportCase, SupportCase.id == Message.case_id)
                .join(Conversation, Conversation.id == SupportCase.conversation_id)
                .where(
                    Conversation.external_id == conversation_id,
                    Conversation.external_id.like("vk:%"),
                    Message.created_at >= day_start,
                    Message.created_at < day_end,
                )
                .order_by(Message.created_at.asc(), Message.id.asc())
            )
            messages = session.scalars(stmt).all()

        return ViewerDialogMessagesResponse(
            conversation_id=meta.conversation_id,
            display_name=meta.display_name,
            day=day,
            messages=[
                ViewerMessageItem(
                    id=str(message.id),
                    sent_at=self._normalize_dt(message.created_at),
                    direction="inbound" if message.role == "user" else "outbound",
                    author_name=(
                        (meta.display_name or meta.external_chat_id)
                        if message.role == "user"
                        else "Оператор VK"
                        if message.role == "human"
                        else "Support Agent"
                    ),
                    text=message.content,
                )
                for message in messages
            ],
        )

    def _load_conversation_meta(self, session: Session, conversation_id: str) -> _ViewerConversationMeta:
        stmt = (
            select(Conversation.external_id, func.max(SupportCase.id).label("case_id"), User.display_name)
            .outerjoin(SupportCase, SupportCase.conversation_id == Conversation.id)
            .outerjoin(User, User.external_id == Conversation.external_id)
            .where(Conversation.external_id == conversation_id, Conversation.external_id.like("vk:%"))
            .group_by(Conversation.external_id, User.display_name)
        )
        row = session.execute(stmt).one_or_none()
        if row is None:
            return _ViewerConversationMeta(
                conversation_id=conversation_id,
                case_id=None,
                display_name=None,
                external_chat_id=self._external_chat_id_from_conversation(conversation_id),
            )
        return _ViewerConversationMeta(
            conversation_id=row.external_id,
            case_id=int(row.case_id) if row.case_id is not None else None,
            display_name=row.display_name,
            external_chat_id=self._external_chat_id_from_conversation(row.external_id),
        )

    def _day_bounds(self, day: date) -> tuple[datetime, datetime]:
        local_day_start = datetime(day.year, day.month, day.day, tzinfo=self.viewer_timezone)
        local_day_end = local_day_start + timedelta(days=1)
        return local_day_start.astimezone(UTC), local_day_end.astimezone(UTC)

    @staticmethod
    def _normalize_dt(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


    @staticmethod
    def _external_chat_id_from_conversation(conversation_id: str) -> str:
        return conversation_id.split(":", 1)[1] if ":" in conversation_id else conversation_id
