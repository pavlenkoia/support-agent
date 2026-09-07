from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = "answer-evidence/v1"


@dataclass(frozen=True)
class Fact:
    id: str
    text: str
    source_refs: list[str]
    conditions: list[str] | None = None
    modality: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "source_refs": list(self.source_refs),
            "conditions": list(self.conditions or []),
            "modality": self.modality,
        }


def typed_fact(
    fact_id: str,
    text: str,
    source_refs: list[str],
    *,
    conditions: list[str] | None = None,
    modality: str | None = None,
) -> dict[str, Any]:
    return Fact(fact_id, text, source_refs, conditions=conditions, modality=modality).as_dict()


def typed_answer_evidence(
    *,
    user_question: str,
    context_scope: str,
    acquisition_status: str,
    facts: list[dict[str, Any]],
    coverage_status: str,
    answered_parts: list[dict[str, Any]] | None = None,
    missing_parts: list[str] | None = None,
    conflicts: list[dict[str, Any]] | None = None,
    unresolved_constraints: list[str] | None = None,
    answer_basis: str = "",
    calendar_facts: list[dict[str, Any]] | None = None,
    policy_evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "user_question": user_question,
        "context_scope": context_scope,
        "acquisition_status": acquisition_status,
        "facts": facts,
        "coverage": {
            "status": coverage_status,
            "answered_parts": answered_parts or [],
            "missing_parts": missing_parts or [],
            "conflicts": conflicts or [],
            "unresolved_constraints": unresolved_constraints or [],
        },
        "answer_basis": answer_basis,
        "calendar_facts": calendar_facts or [],
        "policy_evidence": policy_evidence or [],
    }


def typed_extraction_result(*, grounding_status: str, evidence: dict[str, Any], source_refs: list[str]) -> dict[str, Any]:
    return {
        "grounding_status": grounding_status,
        "grounded_facts": evidence["facts"],
        "answer_basis": evidence.get("answer_basis", ""),
        "source_refs": list(source_refs),
        "answer_evidence": evidence,
        "facts": evidence["facts"],
    }


def migrate_fixture(packet):
    """Explicitly upgrade legacy synthetic evidence fixtures, never runtime inputs."""
    from copy import deepcopy
    result = deepcopy(packet)
    if 'answer_evidence' in result:
        return result
    refs = result.get('cited_source_refs') or result.get('source_refs') or ['synthetic/fixture.md']
    facts = [typed_fact('f'+str(i+1), fact, refs) if isinstance(fact, str) else fact
             for i, fact in enumerate(result.get('grounded_facts', []))]
    coverage = result.get('coverage') or {'status': 'full' if facts else 'none',
        'answered_parts': [{'question_part': 'fixture question', 'fact_ids': [f['id'] for f in facts]}] if facts else [],
        'missing_parts': [], 'conflicts': [], 'unresolved_constraints': []}
    result.update(grounded_facts=facts, source_refs=refs, cited_source_refs=refs, coverage=coverage)
    result.setdefault('needs_customer_clarification', False)
    result.setdefault('answer_basis', '')
    result.setdefault('reason', 'synthetic_evidence')
    result['answer_evidence'] = typed_answer_evidence(user_question='fixture question', context_scope='fixture scope',
        acquisition_status=result.get('grounding_status', 'ready'), facts=facts,
        coverage_status=coverage['status'], answered_parts=coverage['answered_parts'], missing_parts=coverage['missing_parts'],
        conflicts=coverage['conflicts'], unresolved_constraints=coverage['unresolved_constraints'], answer_basis=result['answer_basis'])
    return result
