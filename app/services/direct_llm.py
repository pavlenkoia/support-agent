from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

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

    def classify_turn(self, text: str) -> dict:
        profile = self._load_profile_context()

        if settings.direct_llm_provider == "stub":
            return {
                "turn_type": "knowledge_request",
                "confidence": 0.0,
                "reason": "stub_provider",
            }

        system_prompt = (
            "You classify the user's message for a customer-facing support agent. "
            "Return JSON only. Decide whether the message is just a social opener/small-talk that should be answered naturally without consulting a knowledge base, "
            "or whether it is a real information request that may require the knowledge base. "
            "Do not answer the user here; only classify the turn."
        )
        user_prompt = json.dumps(
            {
                "task": "Classify the incoming user turn before routing.",
                "required_json_schema": {
                    "turn_type": "social_turn|knowledge_request",
                    "confidence": "number 0..1",
                    "reason": "short string",
                },
                "agent_profile": profile,
                "user_message": text,
                "guidance": [
                    "Use social_turn only for greetings, short acknowledgements, or phatic openers that do not need factual lookup.",
                    "Use knowledge_request for questions about services, pricing, rules, scheduling, certificates, or any factual/operational request.",
                ],
            },
            ensure_ascii=False,
        )

        try:
            raw = self.client.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0,
                response_format={"type": "json_object"},
            )
            parsed: dict[str, Any] = json.loads(raw)
        except Exception as exc:
            return {
                "turn_type": "knowledge_request",
                "confidence": 0.0,
                "reason": f"turn_classifier_error:{type(exc).__name__}",
            }

        turn_type = str(parsed.get("turn_type", "knowledge_request")).strip()
        confidence = float(parsed.get("confidence", 0.0))
        reason = str(parsed.get("reason", "turn_classifier"))

        if turn_type not in {"social_turn", "knowledge_request"}:
            turn_type = "knowledge_request"
        return {
            "turn_type": turn_type,
            "confidence": confidence,
            "reason": reason,
        }

    def answer(self, text: str, kb_hits: list[dict], *, allow_general_without_kb: bool = False) -> dict:
        if not kb_hits and not allow_general_without_kb:
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

        if allow_general_without_kb and not kb_hits:
            return self._answer_social_turn(text)

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

    def _answer_social_turn(self, text: str) -> dict:
        profile = self._load_profile_context()
        system_prompt = (
            "You are the customer-facing support agent for this business. "
            "For simple social turns like greetings, short acknowledgements, or phatic openers, reply naturally in Russian in the agent's role and tone. "
            "Do not use any knowledge base facts unless they were provided explicitly in the prompt. "
            "Do not invent policies, prices, schedules, or operational details. "
            "Return JSON only. The wording should be natural rather than canned."
        )
        user_prompt = json.dumps(
            {
                "task": "Respond to a social opener without consulting the knowledge base.",
                "required_json_schema": {
                    "direct_status": "ready|insufficient_confidence",
                    "decision": "answer|handoff",
                    "response_text": "string",
                    "confidence": "number 0..1",
                    "reason": "short string",
                },
                "agent_profile": profile,
                "user_message": text,
                "constraints": [
                    "Answer in Russian.",
                    "Stay in the support-agent role.",
                    "Keep it brief and natural.",
                    "Do not reference KB, search, or handoff unless truly necessary.",
                ],
            },
            ensure_ascii=False,
        )

        try:
            raw = self.client.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=max(self.temperature, 0.35),
                response_format={"type": "json_object"},
            )
            parsed: dict[str, Any] = json.loads(raw)
        except Exception as exc:
            return {
                "direct_status": "insufficient_confidence",
                "response_text": "",
                "used_kb_sources": [],
                "confidence": 0.0,
                "decision": "handoff",
                "reason": f"social_llm_error:{type(exc).__name__}",
            }

        confidence = float(parsed.get("confidence", 0.0))
        response_text = str(parsed.get("response_text", "")).strip()
        decision = str(parsed.get("decision", "handoff"))
        direct_status = str(parsed.get("direct_status", "insufficient_confidence"))
        reason = str(parsed.get("reason", "social_llm"))

        if decision != "answer" or not response_text:
            return {
                "direct_status": "insufficient_confidence",
                "response_text": "",
                "used_kb_sources": [],
                "confidence": min(confidence, 0.69),
                "decision": "handoff",
                "reason": reason,
            }

        return {
            "direct_status": direct_status,
            "response_text": response_text,
            "used_kb_sources": [],
            "confidence": max(confidence, 0.7),
            "decision": "answer",
            "reason": reason,
        }

    def _load_profile_context(self) -> dict:
        profile_path = Path(settings.support_agent_profile_root) / "profile.yaml"
        if not profile_path.exists():
            return {}

        try:
            data = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

        if not isinstance(data, dict):
            return {}
        return data
