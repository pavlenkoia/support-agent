class EscalationService:
    def to_human(self, reason: str) -> dict:
        return {
            "mode": "human_escalation",
            "case_status": "waiting_human",
            "reason": reason,
        }
