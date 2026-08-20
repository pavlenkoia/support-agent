from __future__ import annotations

from app.services.direct_llm import DirectLLMService


class ActionClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[dict] = []

    def generate(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        return self.response

    def get_last_call_info(self) -> dict:
        return {"provider": "test", "model": "test", "usage": {"prompt_tokens": 1}}


class PromptService:
    def load_system_prompt(self) -> str:
        return "Поведенческий контракт агента."


def test_next_action_returns_provider_neutral_wiki_tool_call() -> None:
    client = ActionClient('{"action":"wiki_lookup","arguments":{},"reason":"нужны факты"}')
    service = DirectLLMService(client=client, prompt_service=PromptService())

    action = service.next_action(
        text="Как записаться на тандем?",
        context={"recent_messages": []},
        tool_observations=[],
        allowed_actions=["wiki_lookup", "finish"],
        iteration=1,
    )

    assert action["action"] == "wiki_lookup"
    assert action["arguments"] == {}
    assert action["reason"] == "нужны факты"
    assert action["llm_trace"][0]["step"] == "agent_next_action"
    assert client.calls[0]["response_format"] == {"type": "json_object"}
    assert "wiki_lookup" in str(client.calls[0]["user_prompt"])
