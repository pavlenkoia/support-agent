class DirectLLMService:
    def answer(self, text: str, kb_hits: list[dict]) -> dict:
        return {
            "mode": "direct_answer",
            "answer": f"Stub direct answer for: {text}",
            "kb_hits": kb_hits,
            "confidence": 0.4,
        }
