from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends

from app.api.deps import get_viewer_service
from app.schemas.viewer import ViewerDialogItem, ViewerDialogMessagesResponse
from app.services.viewer_service import ViewerService

router = APIRouter(prefix="/viewer", tags=["viewer"])


@router.get("/dialogs", response_model=list[ViewerDialogItem])
def list_dialogs(day: date, viewer: ViewerService = Depends(get_viewer_service)) -> list[ViewerDialogItem]:
    return viewer.list_dialogs_for_day(day)


@router.get("/dialogs/{conversation_id}/messages", response_model=ViewerDialogMessagesResponse)
def get_dialog_messages(
    conversation_id: str,
    day: date,
    viewer: ViewerService = Depends(get_viewer_service),
) -> ViewerDialogMessagesResponse:
    return viewer.get_dialog_messages_for_day(conversation_id, day)
