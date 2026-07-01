from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ProbeSessionCreateRequest(BaseModel):
    scenario_name: str | None = None
    requested_by: str | None = None


class ProbeSessionListItem(BaseModel):
    session_id: str
    conversation_id: int
    case_id: int
    case_status: str
    route_mode: str
    channel: str = "internal_test"
    is_test: bool = True
    source: str | None = None
    session_type: str | None = None
    scenario_name: str | None = None
    requested_by: str | None = None
    message_count: int
    last_user_message: str | None = None
    last_assistant_message: str | None = None
    last_activity_at: datetime
    created_at: datetime


class ProbeSessionListResponse(BaseModel):
    total: int
    items: list[ProbeSessionListItem]


class ProbeSessionGetResponse(ProbeSessionListItem):
    pass


class ProbeSessionListFilters(BaseModel):
    status: Literal["open", "resolved", "waiting_human", "waiting_hermes"] | None = None
    scenario_name: str | None = None
    requested_by: str | None = None
    source: str | None = None
    session_type: str | None = None
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class ProbeSessionCreateResponse(BaseModel):
    session_id: str
    case_id: int
    created_at: datetime
    channel: str = "internal_test"
    is_test: bool = True
    scenario_name: str | None = None
    requested_by: str | None = None


class ProbeSendMessageRequest(BaseModel):
    text: str = Field(..., min_length=1)


class ProbeMessageItem(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime


class ProbeWaitReplyRequest(BaseModel):
    timeout_sec: int = Field(default=30, ge=1, le=300)


class ProbeWorkflowEventItem(BaseModel):
    id: int
    event_type: str
    actor: str
    payload: dict[str, Any] | None = None
    created_at: datetime


class ProbeSessionMessageResponse(BaseModel):
    session_id: str
    case_id: int
    channel: str = "internal_test"
    is_test: bool = True
    scenario_name: str | None = None
    requested_by: str | None = None
    final_answer: str
    route: dict[str, Any]
    outcome: dict[str, Any]


class ProbeWaitReplyResponse(BaseModel):
    session_id: str
    case_id: int
    channel: str = "internal_test"
    is_test: bool = True
    scenario_name: str | None = None
    requested_by: str | None = None
    received: bool
    final_answer: str
    message: ProbeMessageItem | None = None
    failure_reason: str | None = None


class ProbeSessionMessagesResponse(BaseModel):
    session_id: str
    case_id: int
    channel: str = "internal_test"
    is_test: bool = True
    scenario_name: str | None = None
    requested_by: str | None = None
    messages: list[ProbeMessageItem]


class ProbeSessionTraceResponse(BaseModel):
    session_id: str
    case_id: int
    channel: str = "internal_test"
    is_test: bool = True
    scenario_name: str | None = None
    requested_by: str | None = None
    events: list[ProbeWorkflowEventItem]


class ProbeSessionCloseResponse(BaseModel):
    session_id: str
    case_id: int
    closed: bool
    case_status: str
    channel: str = "internal_test"
    is_test: bool = True
    scenario_name: str | None = None
    requested_by: str | None = None
