from __future__ import annotations

import json
import time
from pathlib import Path

from app.core.config import settings
from app.integrations.vk.longpoll import VKLongPollClient, VKLongPollState
from app.services.vk_gateway import VKGatewayService


class FileLongPollStateStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def load(self) -> VKLongPollState | None:
        if not self.path.exists():
            return None
        raw = self.path.read_text().strip()
        if not raw:
            return None
        data = json.loads(raw)
        return VKLongPollState(server=str(data["server"]), key=str(data["key"]), ts=str(data["ts"]))

    def save(self, state: VKLongPollState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"server": state.server, "key": state.key, "ts": state.ts}))


class LongPollAcker:
    def __init__(self, store: FileLongPollStateStore) -> None:
        self.store = store

    def ack(self, state: VKLongPollState) -> None:
        self.store.save(state)


def process_once(gateway: VKGatewayService, poller: VKLongPollClient, acker: LongPollAcker, state: VKLongPollState | None) -> dict:
    next_state, updates = poller.poll_once(state)
    results = []
    for update in updates:
        results.append(gateway.handle_event(update))
    acker.ack(next_state)
    return {
        "processed": len(updates),
        "results": results,
        "state": {"server": next_state.server, "key": next_state.key, "ts": next_state.ts},
    }


def process_tick(gateway: VKGatewayService, poller: VKLongPollClient, acker: LongPollAcker, state: VKLongPollState | None) -> dict:
    """Journal current VK updates before replaying historical retries."""
    result = process_once(gateway=gateway, poller=poller, acker=acker, state=state)
    recovery_result = gateway.recover_expired_received_events()
    retry_result = gateway.process_due_retries()
    return result | {"recovered": recovery_result["recovered"], "retried": retry_result["processed"]}


def main() -> None:
    gateway = VKGatewayService()
    poller = VKLongPollClient()
    store = FileLongPollStateStore(settings.vk_poll_state_file)
    acker = LongPollAcker(store)

    while True:
        if not settings.vk_enabled:
            print("vk polling disabled: VK_ENABLED is false", flush=True)
            time.sleep(settings.vk_poll_interval_seconds)
            continue
        if not settings.vk_access_token or not settings.vk_group_id:
            print("vk polling disabled: VK_ACCESS_TOKEN or VK_GROUP_ID is not configured", flush=True)
            time.sleep(settings.vk_poll_interval_seconds)
            continue

        current_state = store.load()
        result = process_tick(gateway=gateway, poller=poller, acker=acker, state=current_state)
        print(
            f"vk polling tick processed={result['processed']} recovered={result['recovered']} retried={result['retried']} ts={result['state']['ts']}",
            flush=True,
        )
        time.sleep(settings.vk_poll_interval_seconds)


if __name__ == "__main__":
    main()
