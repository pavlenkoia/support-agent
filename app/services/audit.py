from datetime import UTC, datetime


def build_audit_event(
    case: dict,
    route: dict,
    retrieval: dict,
    outcome: dict,
    turn_classification: dict,
    response_strategy: dict,
) -> dict:
    return {
        "case_id": case["case_id"],
        "conversation_id": case["conversation_id"],
        "user_id": case["user_id"],
        "turn_classifier": {
            "turn_type": turn_classification.get("turn_type"),
            "confidence": turn_classification.get("confidence"),
            "reason": turn_classification.get("reason"),
        },
        "response_strategy": response_strategy,
        "route": route["route"],
        "route_reason": route["route_reason"],
        "route_confidence": route["route_confidence"],
        "kb_status": retrieval["kb_status"],
        "kb_skip_reason": retrieval.get("kb_skip_reason"),
        "outcome_type": outcome["outcome_type"],
        "outcome_status": outcome["outcome_status"],
        "outcome_kind": route.get("outcome_kind"),
        "source_refs": route.get("source_refs", []),
        "logical_llm_call_count": response_strategy.get("logical_llm_call_count"),
        "provider_attempt_count": response_strategy.get("provider_attempt_count"),
        "timestamp": datetime.now(UTC).isoformat(),
    }
