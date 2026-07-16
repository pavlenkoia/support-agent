from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class ViewerDialogItem(BaseModel):
    conversation_id: str
    case_id: int | None = None
    display_name: str | None
    external_chat_id: str
    last_message_at: datetime
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


class ViewerLoginRequest(BaseModel):
    key: str


class ViewerAuthStatus(BaseModel):
    authenticated: bool


class ViewerPushConfig(BaseModel):
    public_key: str


class ViewerPushSubscriptionKeys(BaseModel):
    p256dh: str
    auth: str


class ViewerPushSubscriptionRequest(BaseModel):
    endpoint: str
    keys: ViewerPushSubscriptionKeys
    expiration_time: int | None = None
