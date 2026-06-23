from typing import Any


class BaseLLMClient:
    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        response_format: dict[str, Any] | None = None,
    ) -> str:  # pragma: no cover - interface stub
        raise NotImplementedError
