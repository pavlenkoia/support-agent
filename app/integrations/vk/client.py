from __future__ import annotations

import json
import time
from urllib import error, parse, request

from app.core.config import settings


class VKAPIClient:
    api_base_url = "https://api.vk.com/method/"

    def __init__(
        self,
        *,
        access_token: str | None = None,
        api_version: str | None = None,
        request_timeout_seconds: int | None = None,
        max_retries: int | None = None,
        retry_backoff_seconds: float | None = None,
    ) -> None:
        self.access_token = access_token if access_token is not None else settings.vk_access_token
        self.api_version = api_version or settings.vk_api_version
        self.request_timeout_seconds = request_timeout_seconds or settings.vk_request_timeout_seconds
        self.max_retries = max_retries if max_retries is not None else settings.vk_max_retries
        self.retry_backoff_seconds = retry_backoff_seconds if retry_backoff_seconds is not None else settings.vk_retry_backoff_seconds

    def api_call(self, method: str, params: dict) -> dict:
        if not self.access_token:
            return {"ok": False, "reason": "vk_access_token_not_configured"}

        body = {
            **params,
            "access_token": self.access_token,
            "v": self.api_version,
        }
        url = f"{self.api_base_url}{method}"
        encoded = parse.urlencode(body).encode()
        req = request.Request(url, data=encoded, method="POST")

        for attempt in range(1, self.max_retries + 1):
            try:
                with request.urlopen(req, timeout=self.request_timeout_seconds) as resp:
                    payload = json.loads(resp.read().decode())
                if payload.get("error"):
                    return {"ok": False, "reason": "vk_api_error", **payload}
                return {"ok": True, **payload}
            except error.HTTPError as exc:
                body_text = exc.read().decode()
                if attempt >= self.max_retries:
                    return {"ok": False, "reason": f"http_error:{exc.code}", "body": body_text}
            except Exception as exc:  # pragma: no cover
                if attempt >= self.max_retries:
                    return {"ok": False, "reason": f"transport_error:{exc}"}
            time.sleep(self.retry_backoff_seconds * attempt)

        return {"ok": False, "reason": "vk_api_unknown_failure"}

    def get_longpoll_server(self, group_id: str | int) -> dict:
        return self.api_call("groups.getLongPollServer", {"group_id": str(group_id)})

    def check_longpoll(
        self,
        *,
        server: str,
        key: str,
        ts: str,
        wait: int,
        mode: int | None = None,
        version: int | None = None,
    ) -> dict:
        params = {
            "act": "a_check",
            "key": key,
            "ts": ts,
            "wait": wait,
        }
        if mode is not None:
            params["mode"] = mode
        if version is not None:
            params["version"] = version
        url = f"{server}?{parse.urlencode(params)}"
        req = request.Request(url, method="GET")
        try:
            with request.urlopen(req, timeout=wait + 5) as resp:
                return {"ok": True, **json.loads(resp.read().decode())}
        except error.HTTPError as exc:
            return {"ok": False, "reason": f"http_error:{exc.code}", "body": exc.read().decode()}
        except Exception as exc:  # pragma: no cover
            return {"ok": False, "reason": f"transport_error:{exc}"}

    def send_message(self, *, peer_id: str | int, text: str, random_id: str | int) -> dict:
        return self.api_call(
            "messages.send",
            {
                "peer_id": str(peer_id),
                "message": text,
                "random_id": str(random_id),
            },
        )

    def get_users(self, user_ids: list[str | int], *, fields: list[str] | None = None) -> dict:
        normalized_ids = [str(user_id).strip() for user_id in user_ids if str(user_id).strip()]
        if not normalized_ids:
            return {"ok": True, "response": []}

        params: dict[str, str] = {"user_ids": ",".join(normalized_ids)}
        if fields:
            normalized_fields = [str(field).strip() for field in fields if str(field).strip()]
            if normalized_fields:
                params["fields"] = ",".join(normalized_fields)
        return self.api_call("users.get", params)
