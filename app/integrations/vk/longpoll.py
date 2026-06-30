from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.integrations.vk.client import VKAPIClient


@dataclass
class VKLongPollState:
    server: str
    key: str
    ts: str


class VKLongPollClient:
    def __init__(
        self,
        *,
        api_client: VKAPIClient | None = None,
        group_id: str | None = None,
        wait_seconds: int | None = None,
        mode: int | None = None,
        version: int | None = None,
    ) -> None:
        self.api_client = api_client or VKAPIClient()
        self.group_id = group_id or settings.vk_group_id or ""
        self.wait_seconds = wait_seconds or settings.vk_longpoll_wait_seconds
        self.mode = settings.vk_longpoll_mode if mode is None else mode
        self.version = settings.vk_longpoll_version if version is None else version

    @staticmethod
    def _is_transient_transport_error(response: dict) -> bool:
        reason = str(response.get("reason") or "").lower()
        return reason.startswith("transport_error:")

    def refresh_state(self) -> VKLongPollState:
        response = self.api_client.get_longpoll_server(self.group_id)
        if not response.get("ok"):
            raise RuntimeError(f"failed to get VK long poll server: {response}")
        data = response.get("response") or {}
        return VKLongPollState(server=str(data["server"]), key=str(data["key"]), ts=str(data["ts"]))

    def poll_once(self, state: VKLongPollState | None) -> tuple[VKLongPollState, list[dict]]:
        if state is None:
            state = self.refresh_state()

        response = self.api_client.check_longpoll(
            server=state.server,
            key=state.key,
            ts=state.ts,
            wait=self.wait_seconds,
            mode=self.mode,
            version=self.version,
        )
        if not response.get("ok"):
            if self._is_transient_transport_error(response):
                return state, []
            raise RuntimeError(f"VK long poll request failed: {response}")

        failed = response.get("failed")
        if failed == 1:
            state.ts = str(response["ts"])
            return state, []
        if failed == 2:
            refreshed = self.refresh_state()
            return refreshed, []
        if failed == 3:
            refreshed = self.refresh_state()
            return refreshed, []

        state.ts = str(response.get("ts", state.ts))
        updates = response.get("updates") or []
        return state, updates
