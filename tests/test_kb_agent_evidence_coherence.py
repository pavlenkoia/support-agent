from __future__ import annotations

import json
from pathlib import Path

from app.integrations.llm.base import BaseLLMClient
from app.services.kb_agent import KBAgentService


class Responses(BaseLLMClient):
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)

    def generate(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.0, response_format=None) -> str:
        _ = (system_prompt, user_prompt, temperature, response_format)
        return json.dumps(self.responses.pop(0), ensure_ascii=False)


def test_cited_facts_are_ready_even_when_extractor_mislabels_them_not_found(tmp_path: Path) -> None:
    pricing = tmp_path / "pricing.md"
    pricing.write_text(
        "# Prices\n\nThe authoritative current price source is https://example.test/price.\n",
        encoding="utf-8",
    )
    service = KBAgentService(
        client=Responses(
            [
                {"user_intent": "current price", "information_needs": ["current price source"], "selected_source_refs": [str(pricing)]},
                {"coverage_status": "enough", "missing_facts": [], "additional_source_refs": []},
                {
                    "grounding_status": "not_found",
                    "answer_basis": "The authoritative current price source is https://example.test/price.",
                    "grounded_facts": ["The authoritative current price source is https://example.test/price."],
                    "cited_source_refs": [str(pricing)],
                    "needs_customer_clarification": False,
                    "reason": "dynamic value not embedded",
                },
            ]
        )
    )

    result = service.read(
        "What is the current price?",
        [
            {
                "source_ref": str(pricing),
                "source_path": str(pricing),
                "source_type": "wiki_page",
                "retrieval_mode": "llm_wiki_catalog",
                "kb_architecture": "llm_wiki",
                "text": "# Catalog",
            }
        ],
        require_coverage_review=True,
    )

    assert result["grounding_status"] == "ready"
    assert result["grounded_facts"] == ["The authoritative current price source is https://example.test/price."]
    assert result["source_refs"] == [str(pricing)]
