from __future__ import annotations

import json
from typing import Any

from app.core.config import settings
from app.integrations.llm.base import BaseLLMClient
from app.integrations.llm.factory import get_llm_client


class DirectLLMService:
    def __init__(self, client: BaseLLMClient | None = None) -> None:
        self.client = client or get_llm_client(
            provider=settings.direct_llm_provider,
            base_url=settings.direct_llm_base_url,
            api_key=settings.direct_llm_api_key,
            model=settings.direct_llm_model,
            timeout_seconds=settings.direct_llm_timeout_seconds,
        )
        self.temperature = settings.direct_llm_temperature

    def answer(self, text: str, kb_hits: list[dict]) -> dict:
        if not kb_hits:
            return {
                "direct_status": "insufficient_confidence",
                "response_text": "",
                "used_kb_sources": [],
                "confidence": 0.0,
                "decision": "handoff",
                "reason": "no_kb_hits",
            }

        if settings.direct_llm_provider == "stub":
            return {
                "direct_status": "insufficient_confidence",
                "response_text": f"Stub direct answer for: {text}",
                "used_kb_sources": [hit["source_ref"] for hit in kb_hits],
                "confidence": 0.4,
                "decision": "handoff",
                "reason": "stub_provider",
            }

        kb_context = []
        for hit in kb_hits[:5]:
            kb_context.append({
                "source_ref": hit.get("source_ref"),
                "text": hit.get("text"),
            })

        system_prompt = (
            "You classify whether the support agent can answer safely from the provided knowledge snippets. "
            "Return JSON only. Never invent facts not present in snippets."
        )
        user_prompt = json.dumps(
            {
                "task": "Decide whether to answer directly or hand off.",
                "allowed_decisions": ["answer", "handoff"],
                "required_json_schema": {
                    "direct_status": "ready|insufficient_confidence",
                    "decision": "answer|handoff",
                    "response_text": "string",
                    "confidence": "number 0..1",
                    "reason": "short string",
                },
                "user_message": text,
                "kb_snippets": kb_context,
            },
            ensure_ascii=False,
        )

        try:
            raw = self.client.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=self.temperature,
                response_format={"type": "json_object"},
            )
            parsed: dict[str, Any] = json.loads(raw)
        except Exception as exc:
            return {
                "direct_status": "insufficient_confidence",
                "response_text": "",
                "used_kb_sources": [hit["source_ref"] for hit in kb_hits],
                "confidence": 0.0,
                "decision": "handoff",
                "reason": f"llm_error:{type(exc).__name__}",
            }
        confidence = float(parsed.get("confidence", 0.0))
        response_text = str(parsed.get("response_text", "")).strip()
        used_kb_sources = [hit["source_ref"] for hit in kb_hits]
        direct_status = str(parsed.get("direct_status", "insufficient_confidence"))
        decision = str(parsed.get("decision", "handoff"))
        reason = str(parsed.get("reason", "llm_decision"))

        if decision != "answer":
            confidence = min(confidence, 0.69)
            direct_status = "insufficient_confidence"
            response_text = ""

        response_text = self._apply_safety_overrides(text, kb_hits, response_text)

        return {
            "direct_status": direct_status,
            "response_text": response_text,
            "used_kb_sources": used_kb_sources,
            "confidence": confidence,
            "decision": decision,
            "reason": reason,
        }

    def _apply_safety_overrides(self, text: str, kb_hits: list[dict], response_text: str) -> str:
        lowered_query = text.lower()
        snippet_text = "\n".join(str(hit.get("text", "")) for hit in kb_hits).lower()

        if "сертифик" in lowered_query and "печ" in lowered_query and "распечат" in snippet_text:
            duration_sentence = ""
            if "6 месяцев" in snippet_text:
                duration_sentence = " Срок действия сертификата — 6 месяцев с даты покупки."
            return "Для использования сертификат нужно предъявить в распечатанном виде на аэродроме." + duration_sentence

        return response_text
