from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.api.routes.messages import router as messages_router
from app.api.routes.probe import router as probe_router
from app.api.routes.viewer import router as viewer_router
from app.core.config import settings

app = FastAPI(title=settings.app_name, version="0.1.0")
app.include_router(health_router)
app.include_router(messages_router, prefix="/api/v1")
app.include_router(probe_router, prefix="/api")
app.include_router(viewer_router, prefix="/api")
