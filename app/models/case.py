from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class SupportCase(Base):
    __tablename__ = "support_cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="open", server_default="open")
    route_mode: Mapped[str] = mapped_column(String(64), nullable=False, default="intake", server_default="intake")
    is_test: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=False, server_default="0", index=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    session_type: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    scenario_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    conversation = relationship("Conversation", back_populates="cases")
    messages = relationship("Message", back_populates="support_case")
    workflow_events = relationship("WorkflowEvent", back_populates="support_case")
