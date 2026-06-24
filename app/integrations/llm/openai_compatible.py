from __future__ import annotations

import json
import socket
import time
from typing import Any
from urllib import error, request

from app.integrations.llm.base import BaseLLMClient


class OpenAICompatibleClient(BaseLLMClient):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: int = 30,
        max_retries: int = 0,
        retry_backoff_seconds: float = 0.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if response_format is not None:
            payload["response_format"] = response_format

        req = request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with request.urlopen(req, timeout=self.timeout_seconds) as response:
                    data = json.loads(response.read().decode("utf-8"))
                break
            except error.HTTPError as exc:  # pragma: no cover - network error path
                detail = exc.read().decode("utf-8", errors="ignore")
                should_retry = exc.code in {408, 409, 425, 429} or exc.code >= 500
                if should_retry and attempt < self.max_retries:
                    last_error = RuntimeError(f"LLM HTTP {exc.code}: {detail}")
                    time.sleep(self.retry_backoff_seconds * (2 ** attempt or 1))
                    continue
                raise RuntimeError(f"LLM HTTP {exc.code}: {detail}") from exc
            except (error.URLError, TimeoutError, socket.timeout) as exc:  # pragma: no cover - network error path
                if attempt < self.max_retries:
                    last_error = RuntimeError(f"LLM connection error: {exc}")
                    time.sleep(self.retry_backoff_seconds * (2 ** attempt or 1))
                    continue
                raise RuntimeError(f"LLM connection error: {exc}") from exc
        else:  # pragma: no cover - defensive
            raise last_error or RuntimeError("LLM request failed")

        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:  # pragma: no cover - malformed response path
            raise RuntimeError(f"Malformed LLM response: {data}") from exc


class StubLLMClient(BaseLLMClient):
    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        _ = (system_prompt, temperature, response_format)
        return user_prompt
