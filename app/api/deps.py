from app.services.probe_service import ProbeService
from app.services.routing import RoutingService
from app.services.viewer_push_service import ViewerPushService
from app.services.viewer_service import ViewerService


def get_routing_service() -> RoutingService:
    return RoutingService()


def get_probe_service() -> ProbeService:
    return ProbeService()


def get_viewer_service() -> ViewerService:
    return ViewerService()


def get_viewer_push_service() -> ViewerPushService:
    return ViewerPushService()
