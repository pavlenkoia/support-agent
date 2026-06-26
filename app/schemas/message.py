from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class InboundMessage(BaseModel):
    channel: str = Field(..., examples=["telegram", "vk"])
    external_user_id: str
    external_chat_id: str
    text: str
    external_message_id: str | None = None
    external_event_type: str | None = None
    external_event_id: str | None = None
    received_at: datetime | None = None
    raw_event: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
