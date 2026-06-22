from app.integrations.llm.base import BaseLLMClient


class OpenAICompatibleClient(BaseLLMClient):
    def generate(self, prompt: str) -> str:
        return prompt
