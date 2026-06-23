from app.integrations.llm.base import BaseLLMClient
from app.integrations.llm.openai_compatible import OpenAICompatibleClient, StubLLMClient



def get_llm_client(
    *,
    provider: str,
    base_url: str | None,
    api_key: str | None,
    model: str,
    timeout_seconds: int = 30,
) -> BaseLLMClient:
    normalized_provider = provider.strip().lower()
    if normalized_provider == "stub":
        return StubLLMClient()

    if normalized_provider in {"mistral", "openai_compatible"}:
        if not base_url:
            raise ValueError("LLM base_url is required for provider")
        if not api_key:
            raise ValueError("LLM api_key is required for provider")
        return OpenAICompatibleClient(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
        )

    raise ValueError(f"Unsupported LLM provider: {provider}")
