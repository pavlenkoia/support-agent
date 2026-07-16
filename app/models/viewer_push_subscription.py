from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ViewerPushSubscription(Base):
    __tablename__ = "viewer_push_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    endpoint: Mapped[str] = mapped_column(Text(), nullable=False, unique=True)
    p256dh: Mapped[str] = mapped_column(Text(), nullable=False)
    auth: Mapped[str] = mapped_column(Text(), nullable=False)
    expiration_time: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
