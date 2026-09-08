from __future__ import annotations

from app.services.kb_agent import KBAgentService
from tests.evidence_fixtures import typed_answer_evidence, typed_fact



def test_kb_agent_compacts_surplus_facts_to_direct_coverage() -> None:
    facts = [
        typed_fact(f"f{index}", f"Подтверждённый факт {index}.", ["kb/certificates.md"])
        for index in range(1, 10)
    ]
    coverage = {
        "status": "full",
        "answered_parts": [
            {"question_part": "срок сертификата", "fact_ids": ["f2"]},
            {"question_part": "нужна ли запись", "fact_ids": ["f9"]},
        ],
        "missing_parts": [],
        "conflicts": [],
        "unresolved_constraints": [],
    }

    compacted = KBAgentService._compact_facts_to_coverage(facts, coverage)

    assert [fact["id"] for fact in compacted] == ["f2", "f9"]


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
