import io
import json
import logging
from http.client import IncompleteRead
from urllib import error

from app.integrations.llm.openai_compatible import OpenAICompatibleClient


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
