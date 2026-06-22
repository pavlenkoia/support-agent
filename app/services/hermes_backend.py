class HermesBackendService:
    def analyze(self, text: str, context: dict, kb_hits: list[dict]) -> dict:
        return {
            "mode": "hermes_escalation",
            "answer": None,
            "reason": "Hermes backend stub not yet integrated",
            "kb_hits": kb_hits,
            "context": context,
        }
