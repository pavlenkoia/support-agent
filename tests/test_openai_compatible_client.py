import io
import json
import logging
from http.client import IncompleteRead
from urllib import error

import pytest

from app.integrations.llm.openai_compatible import OpenAICompatibleClient


@pytest.fixture(autouse=True)
def reset_openai_compatible_pool_state() -> None:
    with OpenAICompatibleClient._pool_lock:
        OpenAICompatibleClient._pool_active_key_indexes.clear()
        OpenAICompatibleClient._pool_disabled_key_indexes.clear()
        OpenAICompatibleClient._pool_cooldown_until.clear()


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_openai_compatible_client_retries_transient_http_errors(monkeypatch) -> None:
    calls = {"count": 0}

    def fake_urlopen(req, timeout):
        calls["count"] += 1
        if calls["count"] == 1:
            raise error.HTTPError(
                req.full_url,
                500,
                "server error",
                hdrs=None,
                fp=io.BytesIO(b'{"error":"temporary"}'),
            )
        return FakeResponse({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr("app.integrations.llm.openai_compatible.request.urlopen", fake_urlopen)
    monkeypatch.setattr("app.integrations.llm.openai_compatible.time.sleep", lambda _: None)

    client = OpenAICompatibleClient(
        provider="mistral",
        base_url="https://example.test/v1",
        api_key="token",
        model="test-model",
        timeout_seconds=45,
        max_retries=2,
        retry_backoff_seconds=0.01,
    )

    result = client.generate(system_prompt="sys", user_prompt="usr")

    assert result == "ok"
    assert calls["count"] == 2


def test_openai_compatible_client_retries_transient_rate_limit_on_same_slot(monkeypatch) -> None:
    calls = {"count": 0}

    def fake_urlopen(req, timeout):
        _ = timeout
        calls["count"] += 1
        if calls["count"] == 1:
            raise error.HTTPError(
                req.full_url,
                429,
                "rate limited",
                hdrs=None,
                fp=io.BytesIO(b'{"message":"Rate limit exceeded"}'),
            )
        return FakeResponse({"choices": [{"message": {"content": "ok after retry"}}]})

    monkeypatch.setattr("app.integrations.llm.openai_compatible.request.urlopen", fake_urlopen)
    monkeypatch.setattr("app.integrations.llm.openai_compatible.time.sleep", lambda _: None)
    client = OpenAICompatibleClient(
        provider="mistral",
        base_url="https://example.test/v1",
        api_key="only-token",
        model="test-model",
        max_retries=1,
        retry_backoff_seconds=0.01,
    )

    assert client.generate(system_prompt="sys", user_prompt="usr") == "ok after retry"
    assert calls["count"] == 2
    assert client.get_last_call_info()["attempts"] == 2
    assert client.get_last_call_info()["used_failover"] is False


def test_openai_compatible_client_fails_over_to_next_api_key_on_auth_error(monkeypatch, caplog) -> None:
    seen_auth_headers: list[str] = []

    def fake_urlopen(req, timeout):
        _ = timeout
        seen_auth_headers.append(req.headers["Authorization"])
        if len(seen_auth_headers) == 1:
            raise error.HTTPError(
                req.full_url,
                401,
                "unauthorized",
                hdrs=None,
                fp=io.BytesIO(b'{"error":"invalid_api_key"}'),
            )
        return FakeResponse({"choices": [{"message": {"content": "ok-secondary"}}]})

    monkeypatch.setattr("app.integrations.llm.openai_compatible.request.urlopen", fake_urlopen)
    monkeypatch.setattr("app.integrations.llm.openai_compatible.time.sleep", lambda _: None)

    client = OpenAICompatibleClient(
        provider="mistral",
        base_url="https://example.test/v1",
        api_key="primary-token",
        api_keys=["primary-token", "secondary-token"],
        model="test-model",
        timeout_seconds=45,
        max_retries=1,
        retry_backoff_seconds=0.01,
    )

    with caplog.at_level(logging.WARNING, logger="app.integrations.llm.openai_compatible"):
        result = client.generate(system_prompt="sys", user_prompt="usr")

    assert result == "ok-secondary"
    assert seen_auth_headers == ["Bearer primary-token", "Bearer secondary-token"]
    info = client.get_last_call_info()
    assert info["attempts"] == 2
    assert info["api_key_index"] == 1
    assert info["used_failover"] is True
    assert info["failover_count"] == 1
    assert info["failover_events"] == [
        {
            "from_api_key_index": 0,
            "to_api_key_index": 1,
            "http_status": 401,
            "reason": '{"error":"invalid_api_key"}',
        }
    ]
    assert "llm api key failover triggered" in caplog.text


def test_openai_compatible_client_skips_invalid_and_quota_exhausted_slots_on_next_request(monkeypatch) -> None:
    seen_auth_headers: list[str] = []

    def fake_urlopen(req, timeout):
        _ = timeout
        seen_auth_headers.append(req.headers["Authorization"])
        if req.headers["Authorization"] == "Bearer invalid-token":
            raise error.HTTPError(req.full_url, 401, "unauthorized", hdrs=None, fp=io.BytesIO(b'{"detail":"Unauthorized"}'))
        raise error.HTTPError(req.full_url, 429, "quota exhausted", hdrs=None, fp=io.BytesIO(b'{"message":"Quota exhausted"}'))

    monkeypatch.setattr("app.integrations.llm.openai_compatible.request.urlopen", fake_urlopen)
    client = OpenAICompatibleClient(
        provider="mistral",
        base_url="https://example.test/v1",
        api_key="invalid-token",
        api_keys=["invalid-token", "limited-token"],
        model="test-model",
        max_retries=0,
        rate_limit_cooldown_seconds=60,
    )

    with pytest.raises(RuntimeError, match="HTTP 429"):
        client.generate(system_prompt="sys", user_prompt="first")
    assert seen_auth_headers == ["Bearer invalid-token", "Bearer limited-token"]

    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        client.generate(system_prompt="sys", user_prompt="second")
    assert seen_auth_headers == ["Bearer invalid-token", "Bearer limited-token"]


def test_openai_compatible_client_cools_down_temporarily_unavailable_pool_slots(monkeypatch) -> None:
    seen_auth_headers: list[str] = []
    calls_by_authorization: dict[str, int] = {}

    def fake_urlopen(req, timeout):
        _ = timeout
        authorization = req.headers["Authorization"]
        seen_auth_headers.append(authorization)
        calls_by_authorization[authorization] = calls_by_authorization.get(authorization, 0) + 1
        should_fail = (
            authorization == "Bearer rotation-primary-token" and calls_by_authorization[authorization] == 1
        ) or (
            authorization == "Bearer rotation-secondary-token" and calls_by_authorization[authorization] == 2
        )
        if should_fail:
            raise error.HTTPError(
                req.full_url,
                401,
                "quota unavailable",
                hdrs=None,
                fp=io.BytesIO(b'{"error":"quota unavailable"}'),
            )
        return FakeResponse({"choices": [{"message": {"content": authorization}}]})

    monkeypatch.setattr("app.integrations.llm.openai_compatible.request.urlopen", fake_urlopen)
    client = OpenAICompatibleClient(
        provider="mistral",
        base_url="https://example.test/v1",
        api_key="rotation-primary-token",
        api_keys=["rotation-primary-token", "rotation-secondary-token"],
        model="test-model",
        max_retries=0,
    )

    assert client.generate(system_prompt="sys", user_prompt="first") == "Bearer rotation-secondary-token"
    with pytest.raises(RuntimeError, match="HTTP 401"):
        client.generate(system_prompt="sys", user_prompt="second")
    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        client.generate(system_prompt="sys", user_prompt="third")

    assert seen_auth_headers == [
        "Bearer rotation-primary-token",
        "Bearer rotation-secondary-token",
        "Bearer rotation-secondary-token",
    ]


def test_openai_compatible_clients_share_last_successful_pool_slot(monkeypatch) -> None:
    seen_auth_headers: list[str] = []

    def fake_urlopen(req, timeout):
        _ = timeout
        authorization = req.headers["Authorization"]
        seen_auth_headers.append(authorization)
        if authorization == "Bearer shared-primary-token":
            raise error.HTTPError(
                req.full_url,
                401,
                "quota unavailable",
                hdrs=None,
                fp=io.BytesIO(b'{"error":"quota unavailable"}'),
            )
        return FakeResponse({"choices": [{"message": {"content": authorization}}]})

    monkeypatch.setattr("app.integrations.llm.openai_compatible.request.urlopen", fake_urlopen)
    first_client = OpenAICompatibleClient(
        provider="mistral", base_url="https://example.test/v1", api_key="shared-primary-token",
        api_keys=["shared-primary-token", "shared-secondary-token"], model="test-model", max_retries=0,
    )
    second_client = OpenAICompatibleClient(
        provider="mistral", base_url="https://example.test/v1", api_key="shared-primary-token",
        api_keys=["shared-primary-token", "shared-secondary-token"], model="other-model", max_retries=0,
    )

    assert first_client.generate(system_prompt="sys", user_prompt="first") == "Bearer shared-secondary-token"
    assert second_client.generate(system_prompt="sys", user_prompt="second") == "Bearer shared-secondary-token"
    assert seen_auth_headers == ["Bearer shared-primary-token", "Bearer shared-secondary-token", "Bearer shared-secondary-token"]


def test_openai_compatible_client_retries_transient_http_error_on_same_api_key(monkeypatch) -> None:
    seen_auth_headers: list[str] = []

    def fake_urlopen(req, timeout):
        _ = timeout
        seen_auth_headers.append(req.headers["Authorization"])
        if len(seen_auth_headers) == 1:
            raise error.HTTPError(
                req.full_url,
                500,
                "server error",
                hdrs=None,
                fp=io.BytesIO(b'{"error":"temporary"}'),
            )
        return FakeResponse({"choices": [{"message": {"content": "ok-primary-retry"}}]})

    monkeypatch.setattr("app.integrations.llm.openai_compatible.request.urlopen", fake_urlopen)
    monkeypatch.setattr("app.integrations.llm.openai_compatible.time.sleep", lambda _: None)

    client = OpenAICompatibleClient(
        provider="mistral",
        base_url="https://example.test/v1",
        api_key="primary-token",
        api_keys=["primary-token", "secondary-token"],
        model="test-model",
        timeout_seconds=45,
        max_retries=1,
        retry_backoff_seconds=0.01,
    )

    result = client.generate(system_prompt="sys", user_prompt="usr")

    assert result == "ok-primary-retry"
    assert seen_auth_headers == ["Bearer primary-token", "Bearer primary-token"]
    info = client.get_last_call_info()
    assert info["attempts"] == 2
    assert info["api_key_index"] == 0
    assert info["used_failover"] is False
    assert info["failover_count"] == 0
    assert info["failover_events"] == []


def test_openai_compatible_client_retries_incomplete_response_body(monkeypatch) -> None:
    calls = {"count": 0}

    class IncompleteBodyResponse(FakeResponse):
        def read(self) -> bytes:
            raise IncompleteRead(b'{"choices":', 50)

    def fake_urlopen(req, timeout):
        _ = (req, timeout)
        calls["count"] += 1
        if calls["count"] == 1:
            return IncompleteBodyResponse({})
        return FakeResponse({"choices": [{"message": {"content": "ok-after-retry"}}]})

    monkeypatch.setattr("app.integrations.llm.openai_compatible.request.urlopen", fake_urlopen)
    monkeypatch.setattr("app.integrations.llm.openai_compatible.time.sleep", lambda _: None)

    client = OpenAICompatibleClient(
        provider="mistral",
        base_url="https://example.test/v1",
        api_key="token",
        model="test-model",
        max_retries=1,
        retry_backoff_seconds=0.01,
    )

    assert client.generate(system_prompt="sys", user_prompt="usr") == "ok-after-retry"
    assert calls["count"] == 2


def test_openai_compatible_client_stops_transient_retries_when_retry_deadline_is_exhausted(monkeypatch) -> None:
    calls = {"count": 0}

    class IncompleteBodyResponse(FakeResponse):
        def read(self) -> bytes:
            raise IncompleteRead(b'{"choices":', 50)

    def fake_urlopen(req, timeout):
        _ = (req, timeout)
        calls["count"] += 1
        return IncompleteBodyResponse({})

    monkeypatch.setattr("app.integrations.llm.openai_compatible.request.urlopen", fake_urlopen)

    client = OpenAICompatibleClient(
        provider="mistral",
        base_url="https://example.test/v1",
        api_key="token",
        model="test-model",
        max_retries=3,
        retry_backoff_seconds=1.0,
        retry_deadline_seconds=0.0,
    )

    try:
        client.generate(system_prompt="sys", user_prompt="usr")
    except RuntimeError as exc:
        assert "retry deadline exceeded" in str(exc)
    else:
        raise AssertionError("expected retry deadline failure")

    assert calls["count"] == 1
    assert client.get_last_call_info()["error"] == "retry_deadline_exceeded"
