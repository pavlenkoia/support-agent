from __future__ import annotations

from app.services.kb_agent import KBAgentService
from tests.evidence_fixtures import typed_answer_evidence, typed_fact


def test_kb_agent_returns_typed_grounded_facts_and_answer_evidence() -> None:
    service = KBAgentService(client=None)

    result = service._fallback_grounded_facts(
        [
            {
                "source_ref": "kb/page.md",
                "text": "Полет лучше согласовать заранее. Есть кабина и салон.",
            }
        ],
        reason="stub_grounding",
    )

    assert result["grounding_status"] == "ready"
    assert result["answer_evidence"]["schema_version"] == "answer-evidence/v1"
    assert result["grounded_facts"][0] == typed_fact("f1", "Полет лучше согласовать заранее.", ["kb/page.md"])
    assert result["answer_evidence"]["facts"][0] == typed_fact("f1", "Полет лучше согласовать заранее.", ["kb/page.md"])
    assert result["answer_evidence"]["coverage"]["status"] in {"partial", "full"}
