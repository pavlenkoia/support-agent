class DirectLLMService:
    def answer(self, text: str, kb_hits: list[dict]) -> dict:
        return {
            "direct_status": "insufficient_confidence",
            "response_text": f"Stub direct answer for: {text}",
            "used_kb_sources": [hit["source_ref"] for hit in kb_hits],
            "confidence": 0.4,
        }
