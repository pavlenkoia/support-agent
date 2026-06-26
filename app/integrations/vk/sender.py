from __future__ import annotations

import random
from datetime import UTC, datetime

from app.integrations.vk.client import VKAPIClient


class VKSender:
    def __init__(self, client: VKAPIClient | None = None) -> None:
        self.client = client or VKAPIClient()

    def send_message(self, *, peer_id: str, text: str, random_id: str | None = None) -> dict:
        generated_random_id = random_id or str(random.randint(1, 2_147_483_647))
        response = self.client.send_message(peer_id=peer_id, text=text, random_id=generated_random_id)
        vk_message_id = None
        if response.get("ok"):
            vk_message_id = response.get("response")
        return {
            "ok": bool(response.get("ok")),
            "sent": bool(response.get("ok")),
            "peer_id": peer_id,
            "text": text,
            "random_id": generated_random_id,
            "external_message_id": str(vk_message_id) if vk_message_id is not None else None,
            "vk_response": response,
            "sent_at": datetime.now(UTC),
            **({"reason": response.get("reason")} if response.get("reason") else {}),
        }
