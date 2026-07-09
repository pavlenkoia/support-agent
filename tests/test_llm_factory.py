from app.integrations.llm.factory import get_llm_client
from app.integrations.llm.openai_compatible import OpenAICompatibleClient


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
