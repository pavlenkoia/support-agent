from typing import Any


class BaseLLMClient:
    def get_last_call_info(self) -> dict[str, Any]:
        info = getattr(self, "_last_call_info", None)
        return dict(info) if isinstance(info, dict) else {}

    def _set_last_call_info(self, info: dict[str, Any]) -> None:
        self._last_call_info = dict(info)

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
    ) -> str:  # pragma: no cover - interface stub
        raise NotImplementedError
