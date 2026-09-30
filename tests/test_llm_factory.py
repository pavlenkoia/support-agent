from app.core.config import Settings
from app.integrations.llm.factory import get_llm_client
from app.integrations.llm.openai_compatible import OpenAICompatibleClient
from app.services.routing import RoutingService


def test_get_llm_client_uses_api_keys_list_when_primary_key_not_passed() -> None:
    client = get_llm_client(
        provider="mistral",
        base_url="https://api.mistral.ai/v1",
        api_key=None,
        api_keys=["primary-key", "secondary-key"],
        model="mistral-small",
        timeout_seconds=30,
        max_retries=1,
        retry_backoff_seconds=0.1,
    )

    assert isinstance(client, OpenAICompatibleClient)
    assert client.api_keys == ["primary-key", "secondary-key"]
    assert client.api_key == "primary-key"


def test_direct_writer_factory_receives_thinking_default() -> None:
    client = get_llm_client(provider="openai_compatible", base_url="https://example.test/v1", api_key="token", api_keys=[], model="test-model", default_think=True)
    assert client.default_think is True


def test_thinking_setting_is_off_by_default_and_reads_environment(monkeypatch) -> None:
    monkeypatch.delenv("DIRECT_LLM_THINK_ENABLED", raising=False)
    assert Settings(_env_file=None).direct_llm_think_enabled is False
    monkeypatch.setenv("DIRECT_LLM_THINK_ENABLED", "true")
    assert Settings(_env_file=None).direct_llm_think_enabled is True


def test_routing_passes_thinking_setting_to_direct_writer_only(monkeypatch) -> None:
    import app.services.routing as routing
    captured = []
    def fake_factory(**kwargs):
        captured.append(kwargs)
        return get_llm_client(provider="stub", base_url=None, api_key=None, api_keys=None, model="stub")
    monkeypatch.setattr(routing, "get_llm_client", fake_factory)
    monkeypatch.setattr(routing.settings, "direct_llm_think_enabled", True)
    monkeypatch.setattr(routing.settings, "jev_api_key", "test-key")
    RoutingService(session_factory=lambda: None, answer_engine_mode="jev_selected_fact")
    assert captured[0]["default_think"] is True
