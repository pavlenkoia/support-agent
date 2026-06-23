from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class SupportCase(Base):
    __tablename__ = "support_cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="open", server_default="open")
    route_mode: Mapped[str] = mapped_column(String(64), nullable=False, default="direct_answer", server_default="direct_answer")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    conversation = relationship("Conversation", back_populates="cases")
    messages = relationship("Message", back_populates="support_case")
    workflow_events = relationship("WorkflowEvent", back_populates="support_case")
