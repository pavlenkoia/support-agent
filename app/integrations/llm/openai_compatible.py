from __future__ import annotations

import hashlib
import json
import logging
import random
import threading
import time
from http.client import IncompleteRead
from typing import Any, ClassVar
from urllib import error, request

from app.integrations.llm.base import BaseLLMClient

logger = logging.getLogger(__name__)


class LLMRecoveryExhausted(RuntimeError):
    """All bounded recovery attempts and eligible key slots were exhausted."""


class OpenAICompatibleClient(BaseLLMClient):
    _pool_active_key_indexes: ClassVar[dict[tuple[str, str, tuple[str, ...]], int]] = {}
    _pool_disabled_key_indexes: ClassVar[dict[tuple[str, str, tuple[str, ...]], set[int]]] = {}
    _pool_cooldown_until: ClassVar[dict[tuple[str, str, tuple[str, ...]], dict[int, float]]] = {}
    _pool_lock: ClassVar[threading.Lock] = threading.Lock()

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
        rate_limit_cooldown_seconds: float = 60.0,
        drop_params: bool = False,
    ) -> None:
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        normalized_keys = [key for key in (api_keys or []) if key]
        if api_key not in normalized_keys:
            normalized_keys.insert(0, api_key)
        self.api_keys = normalized_keys or [api_key]
        self.api_key = self.api_keys[0]
        self._pool_key = (
            self.provider,
            self.base_url,
            tuple(hashlib.sha256(key.encode("utf-8")).hexdigest() for key in self.api_keys),
        )
        with self._pool_lock:
            self._pool_active_key_indexes.setdefault(self._pool_key, 0)
            self._pool_disabled_key_indexes.setdefault(self._pool_key, set())
            self._pool_cooldown_until.setdefault(self._pool_key, {})
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))
        self.retry_deadline_seconds = None if retry_deadline_seconds is None else max(0.0, float(retry_deadline_seconds))
        self.rate_limit_cooldown_seconds = max(0.0, float(rate_limit_cooldown_seconds))
        self.drop_params = bool(drop_params)

    def _get_active_key_index(self) -> int:
        with self._pool_lock:
            return self._pool_active_key_indexes.get(self._pool_key, 0)

    def _set_active_key_index(self, index: int) -> None:
        with self._pool_lock:
            self._pool_active_key_indexes[self._pool_key] = index

    def _available_key_indexes(self) -> list[int]:
        now = time.monotonic()
        with self._pool_lock:
            disabled = self._pool_disabled_key_indexes.get(self._pool_key, set())
            cooldowns = self._pool_cooldown_until.setdefault(self._pool_key, {})
            expired = [index for index, until in cooldowns.items() if until <= now]
            for index in expired:
                del cooldowns[index]
            active = self._pool_active_key_indexes.get(self._pool_key, 0)
            ordered = [(active + offset) % len(self.api_keys) for offset in range(len(self.api_keys))]
            return [index for index in ordered if index not in disabled and index not in cooldowns]

    def _disable_key_index(self, index: int) -> None:
        with self._pool_lock:
            self._pool_disabled_key_indexes.setdefault(self._pool_key, set()).add(index)

    def _cool_down_key_index(self, index: int, *, minimum_seconds: float = 0.0) -> None:
        cooldown_seconds = max(self.rate_limit_cooldown_seconds, minimum_seconds)
        if cooldown_seconds <= 0:
            return
        with self._pool_lock:
            self._pool_cooldown_until.setdefault(self._pool_key, {})[index] = time.monotonic() + cooldown_seconds

    def _retry_delay_seconds(self, retry_attempt: int, *, retry_after_seconds: float | None = None) -> float:
        if retry_after_seconds is not None:
            return retry_after_seconds
        base_delay = self.retry_backoff_seconds * (2 ** retry_attempt)
        return random.uniform(base_delay * 0.5, base_delay * 1.5) if base_delay else 0.0

    def _retry_allowed(self, *, deadline: float | None, retry_attempt: int, retry_after_seconds: float | None = None) -> bool:
        delay = self._retry_delay_seconds(retry_attempt, retry_after_seconds=retry_after_seconds)
        if deadline is None:
            if delay > 0:
                time.sleep(delay)
            return True
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return False
        delay = min(delay, remaining)
        if delay > 0:
            time.sleep(delay)
        return time.perf_counter() < deadline

    def _set_retry_deadline_error(self, *, endpoint: str, started: float, total_attempts: int, active_key_index: int, failover_events: list[dict[str, Any]], attempt_diagnostics: list[dict[str, Any]] | None = None) -> None:
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
                "attempt_diagnostics": list(attempt_diagnostics or []),
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
        if exc.code in {401, 402, 403}:
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
            )
        ):
            return True
        return False

    @staticmethod
    def _is_temporary_key_unavailable(detail: str) -> bool:
        normalized_detail = detail.lower()
        return any(marker in normalized_detail for marker in ("quota unavailable", "temporarily unavailable", "exhausted"))

    @staticmethod
    def _summarize_http_detail(detail: str) -> str:
        compact = " ".join(str(detail or "").split())
        return compact[:200]

    @staticmethod
    def _retry_after_seconds(exc: error.HTTPError) -> float | None:
        value = exc.headers.get("Retry-After") if exc.headers else None
        try:
            return max(0.0, float(value)) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _http_reason_class(exc: error.HTTPError, *, failover: bool) -> str:
        if exc.code in {401, 403}:
            return "authentication_failed"
        if exc.code == 402:
            return "payment_required"
        if exc.code == 429:
            return "quota_exhausted" if failover else "rate_limited"
        if exc.code >= 500:
            return "upstream_unavailable"
        return "transient_http"

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        parallel_tool_calls: bool | None = None,
        messages: list[dict[str, Any]] | None = None,
    ) -> str:
        started = time.perf_counter()
        deadline = started + self.retry_deadline_seconds if self.retry_deadline_seconds is not None else None
        endpoint = f"{self.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": temperature,
            "stream": False,
            # Keep customer turns bounded: the provider must return the
            # requested compact response rather than an unbounded reasoning run.
            "think": False,
            "messages": messages if messages is not None else [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if response_format is not None:
            payload["response_format"] = response_format
        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if parallel_tool_calls is not None:
            payload["parallel_tool_calls"] = parallel_tool_calls
        if self.drop_params:
            # LiteLLM-compatible proxies can drop OpenAI parameters that the
            # selected backend model (for example Ollama chat) does not accept.
            payload["drop_params"] = True

        total_attempts = 0
        key_indexes = self._available_key_indexes()
        if not key_indexes:
            self._set_last_call_info({"provider": self.provider, "model": self.model, "endpoint": endpoint, "attempts": 0, "error": "recovery_exhausted:no_available_slots"})
            raise LLMRecoveryExhausted("All LLM API key slots are temporarily unavailable")
        active_key_position = 0
        active_key_index = key_indexes[active_key_position]
        data: dict[str, Any] | None = None
        last_error: Exception | None = None
        failover_events: list[dict[str, Any]] = []
        attempt_diagnostics: list[dict[str, Any]] = []

        while active_key_position < len(key_indexes):
            active_key_index = key_indexes[active_key_position]
            active_api_key = self.api_keys[active_key_index]
            req = self._build_request(endpoint=endpoint, payload=payload, api_key=active_api_key)
            for retry_attempt in range(self.max_retries + 1):
                try:
                    request_timeout = self.timeout_seconds
                    if deadline is not None:
                        remaining = deadline - time.perf_counter()
                        if remaining <= 0:
                            self._set_retry_deadline_error(
                                endpoint=endpoint,
                                started=started,
                                total_attempts=total_attempts,
                                active_key_index=active_key_index,
                                failover_events=failover_events,
                                attempt_diagnostics=attempt_diagnostics,
                            )
                            raise LLMRecoveryExhausted("LLM retry deadline exceeded")
                        request_timeout = min(request_timeout, remaining)
                    total_attempts += 1
                    attempt_info = {"attempt": total_attempts, "timeout_seconds": request_timeout}
                    attempt_diagnostics.append(attempt_info)
                    try:
                        with request.urlopen(req, timeout=request_timeout) as response:
                            data = json.loads(response.read().decode("utf-8"))
                    except Exception as transport_error:
                        attempt_info["error_type"] = type(transport_error).__name__
                        raise
                    self._set_active_key_index(active_key_index)
                    self._set_last_call_info(
                        {
                            "provider": self.provider,
                            "model": self.model,
                            "endpoint": endpoint,
                            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                            "attempts": total_attempts,
                            "attempt_diagnostics": list(attempt_diagnostics),
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
                    retry_after_seconds = self._retry_after_seconds(exc)
                    if exc.code in {401, 402, 403}:
                        if self._is_temporary_key_unavailable(detail):
                            self._cool_down_key_index(active_key_index)
                        else:
                            self._disable_key_index(active_key_index)
                    elif exc.code == 429 and should_fail_over:
                        self._cool_down_key_index(active_key_index)
                    should_retry = (exc.code in {408, 409, 425, 429} or exc.code >= 500) and not should_fail_over
                    if should_retry and retry_attempt < self.max_retries:
                        last_error = RuntimeError(f"LLM HTTP {exc.code}: {detail}")
                        if self._retry_allowed(deadline=deadline, retry_attempt=retry_attempt, retry_after_seconds=retry_after_seconds):
                            continue
                        self._set_retry_deadline_error(
                            endpoint=endpoint,
                            started=started,
                            total_attempts=total_attempts,
                            active_key_index=active_key_index,
                            failover_events=failover_events,
                            attempt_diagnostics=attempt_diagnostics,
                        )
                        raise LLMRecoveryExhausted("LLM retry deadline exceeded") from exc
                    should_fail_over_after_retries = should_fail_over or should_retry
                    if should_retry:
                        self._cool_down_key_index(active_key_index, minimum_seconds=retry_after_seconds or 0.0)
                    if should_fail_over_after_retries and active_key_position + 1 < len(key_indexes):
                        next_key_index = key_indexes[active_key_position + 1]
                        failover_event = {
                            "from_api_key_index": active_key_index,
                            "to_api_key_index": next_key_index,
                            "http_status": exc.code,
                            "reason": self._summarize_http_detail(detail),
                            "reason_class": self._http_reason_class(exc, failover=should_fail_over),
                            "retry_after_seconds": retry_after_seconds,
                        }
                        failover_events.append(failover_event)
                        self._set_active_key_index(next_key_index)
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
                        active_key_position += 1
                        break
                    self._set_last_call_info(
                        {
                            "provider": self.provider,
                            "model": self.model,
                            "endpoint": endpoint,
                            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                            "attempts": total_attempts,
                            "attempt_diagnostics": list(attempt_diagnostics),
                            "api_key_index": active_key_index,
                            "used_failover": bool(failover_events),
                            "failover_count": len(failover_events),
                            "failover_events": list(failover_events),
                            "error": f"HTTP {exc.code}",
                        }
                    )
                    raise LLMRecoveryExhausted(f"LLM HTTP {exc.code}: {detail}") from exc
                except (error.URLError, TimeoutError, IncompleteRead) as exc:  # pragma: no cover - network error path
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
                            attempt_diagnostics=attempt_diagnostics,
                        )
                        raise LLMRecoveryExhausted("LLM retry deadline exceeded") from exc
                    self._set_last_call_info(
                        {
                            "provider": self.provider,
                            "model": self.model,
                            "endpoint": endpoint,
                            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                            "attempts": total_attempts,
                            "attempt_diagnostics": list(attempt_diagnostics),
                            "api_key_index": active_key_index,
                            "used_failover": bool(failover_events),
                            "failover_count": len(failover_events),
                            "failover_events": list(failover_events),
                            "error": type(exc).__name__,
                        }
                    )
                    raise LLMRecoveryExhausted(f"LLM connection error: {exc}") from exc
            else:  # pragma: no cover - defensive
                raise last_error or RuntimeError("LLM request failed")

            if data is not None:
                break
            if active_key_position >= len(key_indexes):
                break
        else:  # pragma: no cover - defensive
            raise last_error or RuntimeError("LLM request failed")

        if data is None:  # pragma: no cover - defensive
            raise last_error or RuntimeError("LLM request failed")

        try:
            message = data["choices"][0]["message"]
            if isinstance(message, dict) and isinstance(message.get("tool_calls"), list):
                return json.dumps({"_native_tool_calls": message["tool_calls"]}, ensure_ascii=False)
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str) and content.strip():
                return content
            # Ollama/OpenAI-compatible reasoning models may put their entire
            # structured completion in reasoning_content and leave content empty.
            reasoning_content = message.get("reasoning_content") or message.get("reasoning")
            if isinstance(reasoning_content, str) and reasoning_content.strip():
                return reasoning_content
            raise RuntimeError("LLM response has neither content nor reasoning_content")
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
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        parallel_tool_calls: bool | None = None,
        messages: list[dict[str, Any]] | None = None,
    ) -> str:
        _ = (system_prompt, temperature, response_format, tools, tool_choice)
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
