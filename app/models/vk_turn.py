from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class VkTurn(Base):
    __tablename__ = "vk_turns"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), nullable=False, index=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("support_cases.id"), nullable=True, index=True)
    first_event_id: Mapped[int] = mapped_column(ForeignKey("transport_events.id"), nullable=False, unique=True)
    last_event_id: Mapped[int] = mapped_column(ForeignKey("transport_events.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open", index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    claim_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
