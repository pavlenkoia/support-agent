from datetime import UTC, datetime


def build_audit_event(case: dict, route: dict, retrieval: dict, outcome: dict) -> dict:
    return {
        "case_id": case["case_id"],
        "conversation_id": case["conversation_id"],
        "user_id": case["user_id"],
        "route": route["route"],
        "route_reason": route["route_reason"],
        "route_confidence": route["route_confidence"],
        "kb_status": retrieval["kb_status"],
        "outcome_type": outcome["outcome_type"],
        "outcome_status": outcome["outcome_status"],
        "timestamp": datetime.now(UTC).isoformat(),
    }
