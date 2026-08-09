import json

from app.integrations.llm.base import BaseLLMClient
from app.services import direct_llm as direct_llm_module
from app.services.direct_llm import DirectLLMService


def test_ready_grounding_is_sent_without_a_second_model_reinterpreting_it(monkeypatch) -> None:
    class InventingFinalizer(BaseLLMClient):
        def __init__(self) -> None:
            self.calls = 0

        def generate(self, **kwargs):
            self.calls += 1
            return json.dumps(
                {
                    "route": "answer",
                    "response_text": "Вам нужно привезти экипировку с собой.",
                    "confidence": 0.9,
                    "reason": "invented_requirement",
                },
                ensure_ascii=False,
            )

    client = InventingFinalizer()
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

    assert client.calls == 0
    assert result["route"] == "answer"
    assert result["response_text"] == (
        "Да, прыгнуть можно. Очки, шлем, комбинезон и перчатки не выдаются. "
        "Их отсутствие само по себе не мешает прыжку."
    )
    assert result["reason"] == "ready_grounding_rendered"
