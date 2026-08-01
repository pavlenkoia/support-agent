from __future__ import annotations

import json
from urllib import error, request


class TelegramBotClient:
    def __init__(self, token: str | None = None) -> None:
        self.token = token

    def _api_call(self, method: str, payload: dict) -> dict:
        if not self.token:
            return {"ok": False, "reason": "telegram_bot_token_not_configured"}

        url = f"https://api.telegram.org/bot{self.token}/{method}"
        body = json.dumps(payload).encode()
        req = request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except error.HTTPError as exc:
            return {
                "ok": False,
                "reason": f"http_error:{exc.code}",
                "body": exc.read().decode(),
            }
        except Exception as exc:  # pragma: no cover
            return {"ok": False, "reason": f"transport_error:{exc}"}

    def send_message(self, chat_id: str, text: str) -> dict:
        result = self._api_call("sendMessage", {"chat_id": chat_id, "text": text})
        return {
            "ok": bool(result.get("ok")),
            "sent": bool(result.get("ok")),
            "telegram_response": result,
            "chat_id": chat_id,
            "text": text,
            **({"reason": result.get("reason")} if result.get("reason") else {}),
        }

    def send_chat_action(self, chat_id: str, action: str = "typing") -> dict:
        result = self._api_call("sendChatAction", {"chat_id": chat_id, "action": action})
        return {
            "ok": bool(result.get("ok")),
            "sent": bool(result.get("ok")),
            "telegram_response": result,
            "chat_id": chat_id,
            "action": action,
            **({"reason": result.get("reason")} if result.get("reason") else {}),
        }

    def get_updates(self, offset: int | None = None, timeout: int = 30) -> dict:
        payload = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        return self._api_call("getUpdates", payload)
