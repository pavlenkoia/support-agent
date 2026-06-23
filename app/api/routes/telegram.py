from fastapi import APIRouter, Depends

from app.api.deps import get_telegram_gateway_service
from app.services.telegram_gateway import TelegramGatewayService

router = APIRouter()


@router.post('/telegram/webhook')
def telegram_webhook(update: dict, gateway: TelegramGatewayService = Depends(get_telegram_gateway_service)) -> dict:
    return gateway.handle_update(update)
