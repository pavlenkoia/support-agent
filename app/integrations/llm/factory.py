from app.integrations.llm.openai_compatible import OpenAICompatibleClient


def get_llm_client() -> OpenAICompatibleClient:
    return OpenAICompatibleClient()
