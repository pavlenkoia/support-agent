from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.deps import get_viewer_push_service, get_viewer_service
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
    ViewerPushConfig,
    ViewerPushSubscriptionRequest,
)
from app.core.config import settings
from app.services.viewer_push_service import ViewerPushService
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


@protected_router.get("/push/config", response_model=ViewerPushConfig)
def viewer_push_config() -> ViewerPushConfig:
    if not settings.viewer_push_enabled or not settings.viewer_push_vapid_public_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Viewer push is not configured.")
    return ViewerPushConfig(public_key=settings.viewer_push_vapid_public_key)


@protected_router.post("/push/subscriptions", status_code=status.HTTP_201_CREATED)
def create_viewer_push_subscription(
    payload: ViewerPushSubscriptionRequest,
    push: ViewerPushService = Depends(get_viewer_push_service),
) -> Response:
    if not settings.viewer_push_enabled:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Viewer push is not configured.")
    created = push.upsert_subscription(
        endpoint=payload.endpoint,
        p256dh=payload.keys.p256dh,
        auth=payload.keys.auth,
        expiration_time=payload.expiration_time,
    )
    return Response(status_code=status.HTTP_201_CREATED if created else status.HTTP_204_NO_CONTENT)


@protected_router.delete("/push/subscriptions", status_code=status.HTTP_204_NO_CONTENT)
def delete_viewer_push_subscription(
    payload: ViewerPushSubscriptionRequest,
    push: ViewerPushService = Depends(get_viewer_push_service),
) -> Response:
    push.disable_subscription(payload.endpoint)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


router.include_router(protected_router)
