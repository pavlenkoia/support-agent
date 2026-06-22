from pydantic import BaseModel, Field


class InboundMessage(BaseModel):
    channel: str = Field(..., examples=["telegram"])
    external_user_id: str
    external_chat_id: str
    text: str
