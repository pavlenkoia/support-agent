from __future__ import annotations

import time
from pathlib import Path

from app.core.config import settings
from app.integrations.telegram.client import TelegramBotClient
from app.services.telegram_gateway import TelegramGatewayService


class FileOffsetStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def load(self) -> int | None:
        if not self.path.exists():
            return None
        text = self.path.read_text().strip()
        if not text:
            return None
        return int(text)

    def save(self, offset: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(str(offset))


class OffsetAcker:
    def __init__(self, store: FileOffsetStore) -> None:
        self.store = store

    def ack(self, offset: int) -> None:
        self.store.save(offset)


def process_once(gateway, poller, acker, offset: int | None) -> dict:
    retry_processed = gateway.process_due_retries()
    updates = poller.get_updates(offset=offset, timeout=settings.telegram_poll_timeout_seconds)
    results = updates.get("result") or []
    next_offset = offset

    for update in results:
        gateway.handle_update(update)
        update_id = update.get("update_id")
        if update_id is not None:
            next_offset = int(update_id) + 1

    if next_offset is not None and next_offset != offset:
        acker.ack(next_offset)

    return {
        "processed": len(results),
        "retry_processed": retry_processed,
        "next_offset": next_offset,
    }


def main() -> None:
    client = TelegramBotClient(token=settings.telegram_bot_token)
    gateway = TelegramGatewayService(sender=client)
    store = FileOffsetStore(settings.telegram_poll_offset_file)
    acker = OffsetAcker(store)

    while True:
        if not settings.telegram_bot_token:
            print("telegram polling disabled: TELEGRAM_BOT_TOKEN is not configured", flush=True)
            time.sleep(settings.telegram_poll_interval_seconds)
            continue

        current_offset = store.load()
        result = process_once(gateway=gateway, poller=client, acker=acker, offset=current_offset)
        print(f"telegram polling tick processed={result['processed']} next_offset={result['next_offset']}", flush=True)
        time.sleep(settings.telegram_poll_interval_seconds)


if __name__ == "__main__":
    main()
