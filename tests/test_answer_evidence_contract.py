from __future__ import annotations

import pytest

from app.services.answer_evidence import EvidenceValidationError, empty_answer_evidence, validate_answer_evidence


def test_empty_answer_evidence_is_valid_and_defaults_not_found() -> None:
    packet = empty_answer_evidence("Как купить полет?", context_scope="booking")

    assert packet["schema_version"] == "answer-evidence/v1"
    assert packet["user_question"] == "Как купить полет?"
    assert packet["context_scope"] == "booking"
    assert packet["acquisition_status"] == "not_found"
    assert packet["facts"] == []
    assert packet["coverage"]["status"] == "none"

    validated = validate_answer_evidence(packet)
    assert validated == packet
    assert validated is not packet


def test_validate_answer_evidence_rejects_textless_fact_and_keeps_precise_reason() -> None:
    packet = empty_answer_evidence("Вопрос", context_scope="scope")
    packet["facts"] = [{"id": "f1", "text": "", "source_refs": [], "conditions": [], "modality": None}]

    with pytest.raises(EvidenceValidationError) as excinfo:
        validate_answer_evidence(packet)

    assert str(excinfo.value) == "textless"


def test_validate_answer_evidence_rejects_unavailable_status_and_preserves_packet_copy() -> None:
    packet = empty_answer_evidence("Вопрос", context_scope="scope")
    packet["acquisition_status"] = "unavailable"
    packet["facts"] = []

    validated = validate_answer_evidence(packet)
    assert validated["acquisition_status"] == "unavailable"
    assert validated is not packet


def test_validate_answer_evidence_rejects_non_envelope_objects() -> None:
    with pytest.raises(EvidenceValidationError) as excinfo:
        validate_answer_evidence([1, 2, 3])

    assert str(excinfo.value) == "unavailable"
