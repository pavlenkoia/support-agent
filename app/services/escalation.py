class EscalationService:
    def to_human(self, reason: str, case: dict, context: dict, retrieval: dict) -> dict:
        return {
            "handoff_status": "queued",
            "handoff_target": "human_queue",
            "handoff_payload": {
                "case_id": case["case_id"],
                "conversation_id": case["conversation_id"],
                "user_id": case["user_id"],
                "user_message": context["user_message"],
                "recent_turns": context["recent_turns"],
                "session_summary": context["session_summary"],
                "case_state": context["case_state"],
                "kb_status": retrieval["kb_status"],
                "kb_snippets": retrieval["kb_snippets"],
                "outcome_context": reason,
            },
        }
