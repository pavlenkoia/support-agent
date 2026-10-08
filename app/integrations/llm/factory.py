from app.integrations.llm.base import BaseLLMClient
from app.integrations.llm.openai_compatible import OpenAICompatibleClient, StubLLMClient


def get_llm_client(
    *,
    provider: str,
    base_url: str | None,
    api_key: str | None,
    api_keys: list[str] | None,
    model: str,
    timeout_seconds: int = 30,
    max_retries: int = 0,
    retry_backoff_seconds: float = 0.0,
    retry_deadline_seconds: float | None = None,
    drop_params: bool = False,
    default_think: bool = False,
    reasoning_effort: str | None = None,
    json_object_prompt_only: bool = False,
    thinking_max_output_tokens: int = 1024,
) -> BaseLLMClient:
    normalized_provider = provider.strip().lower()
    if normalized_provider == "stub":
        return StubLLMClient(provider=normalized_provider, model=model)

    if normalized_provider in {"mistral", "openai_compatible"}:
        if not base_url:
            raise ValueError("LLM base_url is required for provider")
        resolved_api_keys = [key for key in (api_keys or []) if key]
        resolved_api_key = api_key or (resolved_api_keys[0] if resolved_api_keys else None)
        if not resolved_api_key:
            raise ValueError("LLM api_key is required for provider")
        return OpenAICompatibleClient(
            provider=normalized_provider,
            base_url=base_url,
            api_key=resolved_api_key,
            api_keys=resolved_api_keys,
            model=model,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            retry_deadline_seconds=retry_deadline_seconds,
            drop_params=drop_params,
            default_think=default_think,
            reasoning_effort=reasoning_effort,
            json_object_prompt_only=json_object_prompt_only,
            thinking_max_output_tokens=thinking_max_output_tokens,
        )

    raise ValueError(f"Unsupported LLM provider: {provider}")
