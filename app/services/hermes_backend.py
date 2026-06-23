class HermesBackendService:
    def analyze(self, text: str, context: dict, kb_hits: dict) -> dict:
        return {
            "hermes_status": "stub",
            "recommended_action": "escalate_human",
            "reasoning_summary": "Hermes backend stub not yet integrated",
            "response_draft": None,
            "confidence": 0.0,
            "context": context,
            "kb_status": kb_hits["kb_status"],
            "text": text,
        }
