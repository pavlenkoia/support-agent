from fastapi import APIRouter, Depends

from app.api.deps import get_routing_service
from app.schemas.message import InboundMessage
from app.services.routing import RoutingService

router = APIRouter()


@router.post('/messages/inbound')
def inbound_message(payload: InboundMessage, routing: RoutingService = Depends(get_routing_service)) -> dict:
    return routing.handle_inbound(payload)
