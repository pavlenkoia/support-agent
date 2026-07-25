from __future__ import annotations

import json
import logging
import random
import socket
import time
from http.client import IncompleteRead
from typing import Any
from urllib import error, request

from app.integrations.llm.base import BaseLLMClient

logger = logging.getLogger(__name__)


class OpenAICompatibleClient(BaseLLMClient):
    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        api_key: str,
        api_keys: list[str] | None = None,
        model: str,
        timeout_seconds: int = 30,
        max_retries: int = 0,
        retry_backoff_seconds: float = 0.0,
        retry_deadline_seconds: float | None = None,
    ) -> None:
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        normalized_keys = [key for key in (api_keys or []) if key]
        if api_key not in normalized_keys:
            normalized_keys.insert(0, api_key)
        self.api_keys = normalized_keys or [api_key]
        self.api_key = self.api_keys[0]
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))
        self.retry_deadline_seconds = None if retry_deadline_seconds is None else max(0.0, float(retry_deadline_seconds))

    def _retry_delay_seconds(self, retry_attempt: int) -> float:
        base_delay = self.retry_backoff_seconds * (2 ** retry_attempt)
        return random.uniform(base_delay * 0.5, base_delay * 1.5) if base_delay else 0.0

    def _retry_allowed(self, *, deadline: float | None, retry_attempt: int) -> bool:
        if deadline is None:
            return True
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return False
        delay = min(self._retry_delay_seconds(retry_attempt), remaining)
        if delay > 0:
            time.sleep(delay)
        return time.perf_counter() < deadline

    def _set_retry_deadline_error(self, *, endpoint: str, started: float, total_attempts: int, active_key_index: int, failover_events: list[dict[str, Any]]) -> None:
        self._set_last_call_info(
            {
                "provider": self.provider,
                "model": self.model,
                "endpoint": endpoint,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                "attempts": total_attempts,
                "api_key_index": active_key_index,
                "used_failover": bool(failover_events),
                "failover_count": len(failover_events),
                "failover_events": list(failover_events),
                "error": "retry_deadline_exceeded",
            }
        )

    def _build_request(self, *, endpoint: str, payload: dict[str, Any], api_key: str) -> request.Request:
        return request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

    @staticmethod
    def _should_fail_over_on_http_error(exc: error.HTTPError, detail: str) -> bool:
        normalized_detail = detail.lower()
        if exc.code in {401, 403}:
            return True
        if exc.code == 429 and any(
            marker in normalized_detail
            for marker in (
                "invalid_api_key",
                "insufficient_quota",
                "quota",
                "credit",
                "billing",
                "exhausted",
                "rate limit",
                "rate_limit",
            )
        ):
            return True
        return False

    @staticmethod
    def _summarize_http_detail(detail: str) -> str:
        compact = " ".join(str(detail or "").split())
        return compact[:200]

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        started = time.perf_counter()
        deadline = started + self.retry_deadline_seconds if self.retry_deadline_seconds is not None else None
        endpoint = f"{self.base_url}/chat/completions"
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

        total_attempts = 0
        active_key_index = 0
        data: dict[str, Any] | None = None
        last_error: Exception | None = None
        failover_events: list[dict[str, Any]] = []

        while active_key_index < len(self.api_keys):
            active_api_key = self.api_keys[active_key_index]
            req = self._build_request(endpoint=endpoint, payload=payload, api_key=active_api_key)
            for retry_attempt in range(self.max_retries + 1):
                total_attempts += 1
                try:
                    request_timeout = self.timeout_seconds
                    if deadline is not None:
                        remaining = deadline - time.perf_counter()
                        if remaining <= 0 and retry_attempt > 0:
                            self._set_retry_deadline_error(
                                endpoint=endpoint,
                                started=started,
                                total_attempts=total_attempts,
                                active_key_index=active_key_index,
                                failover_events=failover_events,
                            )
                            raise RuntimeError("LLM retry deadline exceeded")
                        if remaining > 0:
                            request_timeout = min(request_timeout, remaining)
                    with request.urlopen(req, timeout=request_timeout) as response:
                        data = json.loads(response.read().decode("utf-8"))
                    self._set_last_call_info(
                        {
                            "provider": self.provider,
                            "model": self.model,
                            "endpoint": endpoint,
                            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                            "attempts": total_attempts,
                            "api_key_index": active_key_index,
                            "used_failover": bool(failover_events),
                            "failover_count": len(failover_events),
                            "failover_events": list(failover_events),
                            "response_format": response_format.get("type") if isinstance(response_format, dict) else None,
                            "usage": {
                                "prompt_tokens": (data.get("usage") or {}).get("prompt_tokens"),
                                "completion_tokens": (data.get("usage") or {}).get("completion_tokens"),
                                "total_tokens": (data.get("usage") or {}).get("total_tokens"),
                            },
                        }
                    )
                    break
                except error.HTTPError as exc:  # pragma: no cover - network error path
                    detail = exc.read().decode("utf-8", errors="ignore")
                    should_fail_over = self._should_fail_over_on_http_error(exc, detail)
                    should_retry = (exc.code in {408, 409, 425, 429} or exc.code >= 500) and not should_fail_over
                    if should_retry and retry_attempt < self.max_retries:
                        last_error = RuntimeError(f"LLM HTTP {exc.code}: {detail}")
                        if self._retry_allowed(deadline=deadline, retry_attempt=retry_attempt):
                            continue
                        self._set_retry_deadline_error(
                            endpoint=endpoint,
                            started=started,
                            total_attempts=total_attempts,
                            active_key_index=active_key_index,
                            failover_events=failover_events,
                        )
                        raise RuntimeError("LLM retry deadline exceeded") from exc
                    if should_fail_over and active_key_index + 1 < len(self.api_keys):
                        failover_event = {
                            "from_api_key_index": active_key_index,
                            "to_api_key_index": active_key_index + 1,
                            "http_status": exc.code,
                            "reason": self._summarize_http_detail(detail),
                        }
                        failover_events.append(failover_event)
                        logger.warning(
                            "llm api key failover triggered provider=%s model=%s from_slot=%s to_slot=%s http_status=%s reason=%s",
                            self.provider,
                            self.model,
                            failover_event["from_api_key_index"],
                            failover_event["to_api_key_index"],
                            failover_event["http_status"],
                            failover_event["reason"],
                        )
                        last_error = RuntimeError(f"LLM HTTP {exc.code}: {detail}")
                        active_key_index += 1
                        break
                    self._set_last_call_info(
                        {
                            "provider": self.provider,
                            "model": self.model,
                            "endpoint": endpoint,
                            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                            "attempts": total_attempts,
                            "api_key_index": active_key_index,
                            "used_failover": bool(failover_events),
                            "failover_count": len(failover_events),
                            "failover_events": list(failover_events),
                            "error": f"HTTP {exc.code}",
                        }
                    )
                    raise RuntimeError(f"LLM HTTP {exc.code}: {detail}") from exc
                except (error.URLError, TimeoutError, socket.timeout, IncompleteRead) as exc:  # pragma: no cover - network error path
                    if retry_attempt < self.max_retries:
                        last_error = RuntimeError(f"LLM connection error: {exc}")
                        if self._retry_allowed(deadline=deadline, retry_attempt=retry_attempt):
                            continue
                        self._set_retry_deadline_error(
                            endpoint=endpoint,
                            started=started,
                            total_attempts=total_attempts,
                            active_key_index=active_key_index,
                            failover_events=failover_events,
                        )
                        raise RuntimeError("LLM retry deadline exceeded") from exc
                    self._set_last_call_info(
                        {
                            "provider": self.provider,
                            "model": self.model,
                            "endpoint": endpoint,
                            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                            "attempts": total_attempts,
                            "api_key_index": active_key_index,
                            "used_failover": bool(failover_events),
                            "failover_count": len(failover_events),
                            "failover_events": list(failover_events),
                            "error": type(exc).__name__,
                        }
                    )
                    raise RuntimeError(f"LLM connection error: {exc}") from exc
            else:  # pragma: no cover - defensive
                raise last_error or RuntimeError("LLM request failed")

            if data is not None:
                break
            if active_key_index >= len(self.api_keys):
                break
        else:  # pragma: no cover - defensive
            raise last_error or RuntimeError("LLM request failed")

        if data is None:  # pragma: no cover - defensive
            raise last_error or RuntimeError("LLM request failed")

        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:  # pragma: no cover - malformed response path
            raise RuntimeError(f"Malformed LLM response: {data}") from exc


class StubLLMClient(BaseLLMClient):
    def __init__(self, *, provider: str = "stub", model: str = "stub") -> None:
        self.provider = provider
        self.model = model

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        _ = (system_prompt, temperature, response_format)
        self._set_last_call_info(
            {
                "provider": self.provider,
                "model": self.model,
                "endpoint": "stub",
                "duration_ms": 0.0,
                "attempts": 1,
                "used_failover": False,
                "failover_count": 0,
                "failover_events": [],
                "response_format": response_format.get("type") if isinstance(response_format, dict) else None,
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
        )
        return user_prompt
