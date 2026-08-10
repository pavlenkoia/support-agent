import json

from app.integrations.llm.base import BaseLLMClient
from app.services import direct_llm as direct_llm_module
from app.services.direct_llm import DirectLLMService


def test_ready_grounding_is_finalized_with_system_prompt_and_compact_evidence(monkeypatch) -> None:
    class RelevantFinalizer(BaseLLMClient):
        def __init__(self) -> None:
            self.calls = 0
            self.payload: dict | None = None

        def generate(self, **kwargs):
            self.calls += 1
            self.payload = json.loads(kwargs["user_prompt"])
            return json.dumps(
                {
                    "route": "answer",
                    "response_text": "Да, прыгнуть можно. Отсутствие этой экипировки само по себе не мешает прыжку.",
                    "confidence": 0.9,
                    "reason": "finalized_from_grounding",
                },
                ensure_ascii=False,
            )

    client = RelevantFinalizer()
    monkeypatch.setattr(direct_llm_module.settings, "direct_llm_provider", "mistral")

    result = DirectLLMService(client=client).respond(
        "Если у меня нет очков, шлема, комбинезона и перчаток, прыгнуть можно?",
        {
            "kb_status": "found",
            "grounding_status": "ready",
            "answer_basis": "Да, прыгнуть можно.",
            "grounded_facts": [
                "Да, прыгнуть можно.",
                "Очки, шлем, комбинезон и перчатки не выдаются.",
                "Их отсутствие само по себе не мешает прыжку.",
            ],
        },
    )

    assert client.calls == 1
    assert client.payload is not None
    assert client.payload["grounding_evidence"]["answer_basis"] == "Да, прыгнуть можно."
    assert result["route"] == "answer"
    assert result["response_text"] == "Да, прыгнуть можно. Отсутствие этой экипировки само по себе не мешает прыжку."
    assert result["reason"] == "finalized_from_grounding"


def test_ready_grounding_renderer_separates_unpunctuated_facts() -> None:
    result = DirectLLMService(client=None)._render_ready_grounding(
        {
            "grounded_facts": [
                "Очки, шлем, комбинезон и перчатки не выдаются",
                "Их отсутствие само по себе не мешает прыжку",
                "Берцы можно взять в прокате на месте",
            ]
        }
    )

    assert result == (
        "Очки, шлем, комбинезон и перчатки не выдаются. "
        "Их отсутствие само по себе не мешает прыжку. Берцы можно взять в прокате на месте."
    )
