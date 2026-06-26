from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class OutboundTransportSend(Base):
    __tablename__ = "outbound_transport_sends"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id"), nullable=True, index=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("support_cases.id"), nullable=True, index=True)
    peer_external_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    random_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    external_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_text: Mapped[str | None] = mapped_column(Text(), nullable=True)
    sent_by: Mapped[str] = mapped_column(String(32), nullable=False, default="bot", server_default="bot")
    send_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", server_default="pending")
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
