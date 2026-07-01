from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_probe_service
from app.schemas.probe import (
    ProbeSendMessageRequest,
    ProbeSessionCloseResponse,
    ProbeSessionCreateRequest,
    ProbeSessionCreateResponse,
    ProbeSessionGetResponse,
    ProbeSessionListResponse,
    ProbeSessionMessageResponse,
    ProbeSessionMessagesResponse,
    ProbeSessionTraceResponse,
    ProbeWaitReplyRequest,
    ProbeWaitReplyResponse,
)
from app.services.probe_service import ProbeService

router = APIRouter(prefix="/internal/probe", tags=["probe"])


@router.get("/sessions", response_model=ProbeSessionListResponse)
def list_probe_sessions(
    status: str | None = Query(default=None),
    scenario_name: str | None = Query(default=None),
    requested_by: str | None = Query(default=None),
    source: str | None = Query(default=None),
    session_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    probe: ProbeService = Depends(get_probe_service),
) -> ProbeSessionListResponse:
    return ProbeSessionListResponse(**probe.list_sessions(
        status=status,
        scenario_name=scenario_name,
        requested_by=requested_by,
        source=source,
        session_type=session_type,
        limit=limit,
        offset=offset,
    ))


@router.post("/sessions", response_model=ProbeSessionCreateResponse)
def start_probe_session(
    payload: ProbeSessionCreateRequest,
    probe: ProbeService = Depends(get_probe_service),
) -> ProbeSessionCreateResponse:
    return ProbeSessionCreateResponse(**probe.start_session(
        scenario_name=payload.scenario_name,
        requested_by=payload.requested_by,
    ))


@router.post("/sessions/{session_id}/messages", response_model=ProbeSessionMessageResponse)
def send_probe_message(
    session_id: str,
    payload: ProbeSendMessageRequest,
    probe: ProbeService = Depends(get_probe_service),
) -> ProbeSessionMessageResponse:
    try:
        return ProbeSessionMessageResponse(**probe.send_message(session_id, payload.text))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/sessions/{session_id}", response_model=ProbeSessionGetResponse)
def get_probe_session(
    session_id: str,
    probe: ProbeService = Depends(get_probe_service),
) -> ProbeSessionGetResponse:
    try:
        return ProbeSessionGetResponse(**probe.get_session(session_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/sessions/{session_id}/messages", response_model=ProbeSessionMessagesResponse)
def get_probe_messages(
    session_id: str,
    probe: ProbeService = Depends(get_probe_service),
) -> ProbeSessionMessagesResponse:
    try:
        return ProbeSessionMessagesResponse(**probe.get_messages(session_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/wait-reply", response_model=ProbeWaitReplyResponse)
def wait_probe_reply(
    session_id: str,
    payload: ProbeWaitReplyRequest,
    probe: ProbeService = Depends(get_probe_service),
) -> ProbeWaitReplyResponse:
    try:
        return ProbeWaitReplyResponse(**probe.wait_reply(session_id, timeout_sec=payload.timeout_sec))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/sessions/{session_id}/trace", response_model=ProbeSessionTraceResponse)
def get_probe_trace(
    session_id: str,
    probe: ProbeService = Depends(get_probe_service),
) -> ProbeSessionTraceResponse:
    try:
        return ProbeSessionTraceResponse(**probe.get_trace(session_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/close", response_model=ProbeSessionCloseResponse)
def close_probe_session(
    session_id: str,
    probe: ProbeService = Depends(get_probe_service),
) -> ProbeSessionCloseResponse:
    try:
        return ProbeSessionCloseResponse(**probe.close_session(session_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
