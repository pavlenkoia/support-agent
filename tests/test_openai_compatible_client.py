import io
import json
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
