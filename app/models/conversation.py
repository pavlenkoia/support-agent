from __future__ import annotations

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="active", server_default="active")
    is_test: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=False, server_default="0", index=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    session_type: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    scenario_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    cases = relationship("SupportCase", back_populates="conversation")
