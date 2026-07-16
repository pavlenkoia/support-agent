from __future__ import annotations

import time

from app.core.config import settings
from app.services.viewer_push_service import ViewerPushService


def main() -> None:
    service = ViewerPushService()
    while True:
        if not settings.viewer_push_enabled:
            print("viewer push disabled: VIEWER_PUSH_ENABLED is false", flush=True)
            time.sleep(settings.viewer_push_poll_interval_seconds)
            continue
        processed = service.process_once()
        if processed:
            print("viewer push event delivered", flush=True)
            continue
        time.sleep(settings.viewer_push_poll_interval_seconds)


if __name__ == "__main__":
    main()
