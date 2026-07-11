from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import get_viewer_service
from app.api.viewer_auth import (
    clear_viewer_auth_cookie,
    get_viewer_auth_context,
    require_viewer_auth,
    set_viewer_auth_cookie,
    verify_viewer_key,
)
from app.schemas.viewer import (
    ViewerAuthStatus,
    ViewerDialogItem,
    ViewerDialogMessagesResponse,
    ViewerLoginRequest,
)
from app.services.viewer_service import ViewerService

router = APIRouter(prefix="/viewer", tags=["viewer"])
protected_router = APIRouter(dependencies=[Depends(require_viewer_auth)])


@router.post("/auth/login", status_code=status.HTTP_204_NO_CONTENT)
def login_viewer(payload: ViewerLoginRequest) -> Response:
    verify_viewer_key(payload.key)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    set_viewer_auth_cookie(response)
    return response


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout_viewer() -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_viewer_auth_cookie(response)
    return response


@router.get("/auth/me", response_model=ViewerAuthStatus)
def viewer_auth_status(context=Depends(get_viewer_auth_context)) -> ViewerAuthStatus:
    return ViewerAuthStatus(authenticated=context.authenticated)


@protected_router.get("/dialogs", response_model=list[ViewerDialogItem])
def list_dialogs(day: date, viewer: ViewerService = Depends(get_viewer_service)) -> list[ViewerDialogItem]:
    return viewer.list_dialogs_for_day(day)


@protected_router.get("/dialogs/{conversation_id}/messages", response_model=ViewerDialogMessagesResponse)
def get_dialog_messages(
    conversation_id: str,
    day: date,
    viewer: ViewerService = Depends(get_viewer_service),
) -> ViewerDialogMessagesResponse:
    return viewer.get_dialog_messages_for_day(conversation_id, day)


router.include_router(protected_router)
