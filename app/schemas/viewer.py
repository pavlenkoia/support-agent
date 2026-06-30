from __future__ import annotations

from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict


class ViewerDialogItem(BaseModel):
    conversation_id: str
    display_name: str | None
    external_chat_id: str
    last_message_time: time
    message_count: int


class ViewerMessageItem(BaseModel):
    id: str
    sent_at: datetime
    direction: str
    author_name: str | None
    text: str


class ViewerDialogMessagesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    conversation_id: str
    display_name: str | None
    day: date
    messages: list[ViewerMessageItem]
