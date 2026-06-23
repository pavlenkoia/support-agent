from app.schemas.message import InboundMessage


def build_context(payload: InboundMessage, case: dict) -> dict:
    return {
        "user_message": payload.text,
        "recent_turns": [payload.text],
        "session_summary": None,
        "case_state": {
            "case_id": case["case_id"],
            "case_status": case["case_status"],
            "conversation_id": case["conversation_id"],
        },
    }
