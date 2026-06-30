from app.services.routing import RoutingService
from app.services.viewer_service import ViewerService


def get_routing_service() -> RoutingService:
    return RoutingService()


def get_viewer_service() -> ViewerService:
    return ViewerService()
