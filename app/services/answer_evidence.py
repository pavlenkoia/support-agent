from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

SCHEMA_VERSION = "answer-evidence/v1"
MAX_PACKET_BYTES = 16384
MAX_FACTS = 8
MAX_SOURCE_REFS = 5
MAX_LIST_ITEMS = 16
MAX_CONFLICTS = 8
ALLOWED_ACQUISITION_STATUSES = {"ready", "not_found", "unavailable"}
ALLOWED_COVERAGE_STATUSES = {"full", "partial", "none", "ambiguous", "conflicting"}
ALLOWED_FACT_KEYS = {"id", "text", "source_refs", "conditions", "modality"}
ALLOWED_COVERAGE_KEYS = {"status", "answered_parts", "missing_parts", "conflicts", "unresolved_constraints"}
ALLOWED_PACKET_KEYS = {
    "schema_version",
    "user_question",
    "context_scope",
    "acquisition_status",
    "facts",
    "coverage",
    "answer_basis",
    "calendar_facts",
    "policy_evidence",
}


class EvidenceValidationError(ValueError):
    pass


def empty_answer_evidence(
    user_question: str,
    context_scope: str = "",
    acquisition_status: str = "not_found",
) -> dict[str, Any]:
    packet = {
        "schema_version": SCHEMA_VERSION,
        "user_question": user_question,
        "context_scope": context_scope,
        "acquisition_status": acquisition_status,
        "facts": [],
        "coverage": {
            "status": "none",
            "answered_parts": [],
            "missing_parts": [],
            "conflicts": [],
            "unresolved_constraints": [],
        },
        "answer_basis": "",
        "calendar_facts": [],
        "policy_evidence": [],
    }
    return validate_answer_evidence(packet)


def build_answer_evidence(packet: dict[str, Any], *, selected_source_refs: list[str] | None = None) -> dict[str, Any]:
    return validate_answer_evidence(packet, selected_source_refs=selected_source_refs)


def validate_answer_evidence(packet: dict[str, Any], *, selected_source_refs: list[str] | None = None) -> dict[str, Any]:
    if not isinstance(packet, dict):
        raise EvidenceValidationError("unavailable")
    if set(packet) - ALLOWED_PACKET_KEYS:
        raise EvidenceValidationError("evidence_unavailable")
    required = ["schema_version", "user_question", "context_scope", "acquisition_status", "facts", "coverage", "answer_basis", "calendar_facts", "policy_evidence"]
    for key in required:
        if key not in packet:
            raise EvidenceValidationError("evidence_unavailable")
    if packet["schema_version"] != SCHEMA_VERSION:
        raise EvidenceValidationError("evidence_unavailable")
    if not isinstance(packet["user_question"], str):
        raise EvidenceValidationError("evidence_unavailable")
    if not isinstance(packet["context_scope"], str):
        raise EvidenceValidationError("evidence_unavailable")
    if not isinstance(packet["acquisition_status"], str) or packet["acquisition_status"] not in ALLOWED_ACQUISITION_STATUSES:
        raise EvidenceValidationError("evidence_unavailable")
    if not isinstance(packet["answer_basis"], str):
        raise EvidenceValidationError("evidence_unavailable")

    facts = _normalize_facts(packet["facts"])
    coverage = _normalize_coverage(packet["coverage"], fact_ids={fact["id"] for fact in facts})
    if coverage["status"] == "full" and (
        packet["acquisition_status"] != "ready" or not facts or not coverage["answered_parts"]
        or any(not part["fact_ids"] for part in coverage["answered_parts"])
        or coverage["missing_parts"] or coverage["conflicts"] or coverage["unresolved_constraints"]
    ):
        raise EvidenceValidationError("evidence_coverage_inconsistent")
    calendar_facts = _normalize_structured_evidence_list(packet["calendar_facts"], field="calendar_facts", required_keys={"kind", "summary", "source_ref", "structured"})
    policy_evidence = _normalize_structured_evidence_list(packet["policy_evidence"], field="policy_evidence", required_keys={"kind", "summary", "source_ref"})

    if len(facts) > MAX_FACTS:
        raise EvidenceValidationError("evidence_too_large")
    for fact in facts:
        if len(fact["source_refs"]) > MAX_SOURCE_REFS:
            raise EvidenceValidationError("evidence_unavailable")
    if len(coverage["answered_parts"]) > MAX_LIST_ITEMS or len(coverage["missing_parts"]) > MAX_LIST_ITEMS or len(coverage["unresolved_constraints"]) > MAX_LIST_ITEMS:
        raise EvidenceValidationError("evidence_unavailable")
    if len(coverage["conflicts"]) > MAX_CONFLICTS:
        raise EvidenceValidationError("evidence_unavailable")

    if selected_source_refs is not None:
        selected = {str(ref) for ref in selected_source_refs if isinstance(ref, str)}
        for fact in facts:
            if not set(fact["source_refs"]).issubset(selected):
                raise EvidenceValidationError("evidence_unavailable")

    validated = {
        "schema_version": packet["schema_version"],
        "user_question": packet["user_question"],
        "context_scope": packet["context_scope"],
        "acquisition_status": packet["acquisition_status"],
        "facts": facts,
        "coverage": coverage,
        "answer_basis": packet["answer_basis"],
        "calendar_facts": calendar_facts,
        "policy_evidence": policy_evidence,
    }
    if _packet_size_bytes(validated) > MAX_PACKET_BYTES:
        raise EvidenceValidationError("evidence_packet_too_large")
    return deepcopy(validated)


def allowed_answer_routes(
    packet: dict[str, Any], *, wiki_executed: bool, calendar_executed: bool,
    response_intent: str,
) -> list[str]:
    """Constrain declared evidence after collection; never infer business meaning."""
    packet = validate_answer_evidence(packet)
    if packet["acquisition_status"] == "unavailable":
        raise EvidenceValidationError("evidence_unavailable")
    if response_intent == "social_reply":
        return ["social_reply"]
    if wiki_executed:
        coverage = packet["coverage"]
        if coverage["missing_parts"] or coverage["conflicts"] or coverage["unresolved_constraints"]:
            return ["cannot_answer"]
        if coverage["status"] == "full":
            return ["answer", "social_reply", "cannot_answer", "clarification_requested", "out_of_scope"]
        if coverage["status"] == "ambiguous":
            return ["clarification_requested", "cannot_answer"]
        return ["cannot_answer"]
    if calendar_executed and packet["calendar_facts"]:
        return ["answer", "cannot_answer", "clarification_requested"]
    if response_intent == "clarification":
        return ["clarification_requested", "cannot_answer"]
    return ["cannot_answer"]


def _normalize_facts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise EvidenceValidationError("evidence_unavailable")
    facts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) - ALLOWED_FACT_KEYS:
            raise EvidenceValidationError("evidence_unavailable")
        for key in ALLOWED_FACT_KEYS:
            if key not in item:
                raise EvidenceValidationError("evidence_unavailable")
        if not isinstance(item["id"], str) or not item["id"] or item["id"] in seen:
            raise EvidenceValidationError("evidence_unavailable")
        seen.add(item["id"])
        if not isinstance(item["text"], str) or not item["text"].strip():
            raise EvidenceValidationError("textless")
        if not isinstance(item["source_refs"], list) or not item["source_refs"]:
            raise EvidenceValidationError("evidence_unavailable")
        if len(item["source_refs"]) > MAX_SOURCE_REFS:
            raise EvidenceValidationError("evidence_unavailable")
        source_refs = []
        for ref in item["source_refs"]:
            if not isinstance(ref, str) or not ref.strip():
                raise EvidenceValidationError("evidence_unavailable")
            source_refs.append(ref)
        if not isinstance(item["conditions"], list):
            raise EvidenceValidationError("evidence_unavailable")
        conditions = []
        for cond in item["conditions"]:
            if not isinstance(cond, str):
                raise EvidenceValidationError("evidence_unavailable")
            conditions.append(cond)
        modality = item["modality"]
        if modality is not None and not isinstance(modality, str):
            raise EvidenceValidationError("evidence_unavailable")
        facts.append({"id": item["id"], "text": item["text"], "source_refs": source_refs, "conditions": conditions, "modality": modality})
    return facts


def _normalize_coverage(value: Any, *, fact_ids: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - ALLOWED_COVERAGE_KEYS:
        raise EvidenceValidationError("evidence_unavailable")
    if not isinstance(value.get("status"), str) or value.get("status") not in ALLOWED_COVERAGE_STATUSES:
        raise EvidenceValidationError("evidence_unavailable")
    answered_parts = _normalize_answered_parts(value.get("answered_parts"), fact_ids=fact_ids)
    missing_parts = _normalize_string_list(value.get("missing_parts"), max_items=MAX_LIST_ITEMS)
    conflicts = _normalize_conflicts(value.get("conflicts"), fact_ids=fact_ids)
    unresolved_constraints = _normalize_string_list(value.get("unresolved_constraints"), max_items=MAX_LIST_ITEMS)
    return {"status": value["status"], "answered_parts": answered_parts, "missing_parts": missing_parts, "conflicts": conflicts, "unresolved_constraints": unresolved_constraints}


def _normalize_answered_parts(value: Any, *, fact_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise EvidenceValidationError("evidence_unavailable")
    if len(value) > MAX_LIST_ITEMS:
        raise EvidenceValidationError("evidence_unavailable")
    parts = []
    for item in value:
        if not isinstance(item, dict) or set(item) - {"question_part", "fact_ids"}:
            raise EvidenceValidationError("evidence_unavailable")
        if not isinstance(item.get("question_part"), str):
            raise EvidenceValidationError("evidence_unavailable")
        fact_list = _normalize_string_list(item.get("fact_ids"), max_items=MAX_LIST_ITEMS)
        if any(fid not in fact_ids for fid in fact_list):
            raise EvidenceValidationError("evidence_unavailable")
        parts.append({"question_part": item["question_part"], "fact_ids": fact_list})
    return parts


def _normalize_conflicts(value: Any, *, fact_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise EvidenceValidationError("evidence_unavailable")
    if len(value) > MAX_CONFLICTS:
        raise EvidenceValidationError("evidence_unavailable")
    conflicts = []
    for item in value:
        if not isinstance(item, dict) or set(item) - {"fact_ids", "description"}:
            raise EvidenceValidationError("evidence_unavailable")
        if not isinstance(item.get("description"), str):
            raise EvidenceValidationError("evidence_unavailable")
        fact_list = _normalize_string_list(item.get("fact_ids"), max_items=MAX_LIST_ITEMS)
        if any(fid not in fact_ids for fid in fact_list):
            raise EvidenceValidationError("evidence_unavailable")
        conflicts.append({"fact_ids": fact_list, "description": item["description"]})
    return conflicts


def _normalize_structured_evidence_list(value: Any, *, field: str, required_keys: set[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise EvidenceValidationError("evidence_unavailable")
    items: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) - required_keys:
            raise EvidenceValidationError("evidence_unavailable")
        if not required_keys.issubset(item):
            raise EvidenceValidationError("evidence_unavailable")
        if any(not isinstance(item[k], str) or not item[k].strip() for k in ("kind", "summary", "source_ref")):
            raise EvidenceValidationError("evidence_field_type_invalid")
        if field == "policy_evidence" and item["kind"] != "profile_no_answer_option":
            raise EvidenceValidationError("evidence_policy_kind_invalid")
        if field == "calendar_facts":
            if item["kind"] not in {"calendar_lookup", "calendar_weekday", "calendar_period_weekends", "calendar_period_public"}:
                raise EvidenceValidationError("evidence_calendar_kind_invalid")
            structured = item["structured"]
            allowed = {"iso_date", "weekday_ru", "is_weekend", "year", "weekday_index", "original_period", "months", "start_month", "end_month", "weekend_dates"}
            if not isinstance(structured, dict) or set(structured) - allowed:
                raise EvidenceValidationError("evidence_calendar_fields_invalid")
            for key, value in structured.items():
                valid = (isinstance(value, str) if key in {"iso_date", "weekday_ru", "original_period"} else
                         type(value) is bool if key == "is_weekend" else
                         isinstance(value, list) and all(type(v) is int for v in value) if key == "months" else
                         isinstance(value, list) and all(isinstance(v, str) for v in value) if key == "weekend_dates" else
                         type(value) is int)
                if not valid:
                    raise EvidenceValidationError("evidence_calendar_field_type_invalid")
        items.append(deepcopy(item))
    return items


def _normalize_string_list(value: Any, *, max_items: int) -> list[str]:
    if not isinstance(value, list):
        raise EvidenceValidationError("evidence_unavailable")
    if len(value) > max_items:
        raise EvidenceValidationError("evidence_unavailable")
    out = []
    for item in value:
        if not isinstance(item, str):
            raise EvidenceValidationError("evidence_unavailable")
        out.append(item)
    return out


def _packet_size_bytes(packet: dict[str, Any]) -> int:
    try:
        return len(json.dumps(packet, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise EvidenceValidationError("evidence_field_type_invalid") from exc
