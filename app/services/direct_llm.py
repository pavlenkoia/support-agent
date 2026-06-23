from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from app.core.config import settings
from app.integrations.llm.base import BaseLLMClient
from app.integrations.llm.factory import get_llm_client


MAX_CATALOG_SELECTION = 3
MAX_REVIEW_ADDITIONS = 2
MAX_SELECTED_WIKI_PAGES = 5


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

        kb_context, kb_mode = self._prepare_kb_context(kb_hits)
        answer_context = kb_context
        answer_mode = kb_mode
        engine_trace: dict[str, Any] = {}

        if kb_mode == "llm_wiki_catalog":
            answer_context, answer_mode, engine_trace = self._run_llm_wiki_engine(text, kb_context)

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
                "kb_mode": answer_mode,
                "guidance": [
                    "Use only facts grounded in the provided wiki content.",
                    "If kb_mode is llm_wiki_navigation_selected_pages, answer only from the selected full wiki pages.",
                    "If the wiki contains enough information to answer safely, answer directly in Russian and do not hand off.",
                ],
                "wiki_trace": engine_trace,
                "kb_snippets": answer_context,
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
                "used_kb_sources": self._extract_source_refs(answer_context) or [hit["source_ref"] for hit in kb_hits],
                "confidence": 0.0,
                "decision": "handoff",
                "reason": f"llm_error:{type(exc).__name__}",
            }

        confidence = float(parsed.get("confidence", 0.0))
        response_text = str(parsed.get("response_text", "")).strip()
        used_kb_sources = self._extract_source_refs(answer_context) or [hit["source_ref"] for hit in kb_hits]
        direct_status = str(parsed.get("direct_status", "insufficient_confidence"))
        decision = str(parsed.get("decision", "handoff"))
        reason = str(parsed.get("reason", "llm_decision"))

        if decision != "answer":
            confidence = min(confidence, 0.69)
            direct_status = "insufficient_confidence"
            response_text = ""

        response_text = self._apply_safety_overrides(text, answer_context, response_text)

        return {
            "direct_status": direct_status,
            "response_text": response_text,
            "used_kb_sources": used_kb_sources,
            "confidence": confidence,
            "decision": decision,
            "reason": reason,
        }

    def _prepare_kb_context(self, kb_hits: list[dict]) -> tuple[list[dict], str]:
        kb_mode = "retrieved_snippets"
        use_catalog = any(hit.get("retrieval_mode") == "llm_wiki_catalog" for hit in kb_hits)
        selected_hits = kb_hits if use_catalog else kb_hits[:5]
        if use_catalog:
            kb_mode = "llm_wiki_catalog"

        kb_context: list[dict] = []
        for hit in selected_hits:
            kb_context.append(
                {
                    "source_ref": hit.get("source_ref"),
                    "source_type": hit.get("source_type"),
                    "page_title": hit.get("page_title"),
                    "linked_pages": hit.get("linked_pages", []),
                    "page_summary": hit.get("page_summary"),
                    "page_preview": hit.get("page_preview"),
                    "retrieval_notes": hit.get("retrieval_notes"),
                    "text": hit.get("text"),
                }
            )
        return kb_context, kb_mode

    def _run_llm_wiki_engine(self, text: str, kb_context: list[dict]) -> tuple[list[dict], str, dict[str, Any]]:
        navigation = self._plan_llm_wiki_navigation(text, kb_context)
        selected_refs = self._normalize_catalog_refs(
            navigation.get("selected_source_refs", []),
            kb_context,
            limit=MAX_CATALOG_SELECTION,
        )
        if not selected_refs:
            selected_refs = self._fallback_select_catalog_refs(text, kb_context, limit=MAX_CATALOG_SELECTION)

        loaded_pages = self._load_catalog_pages(kb_context, selected_refs)
        review = self._review_llm_wiki_coverage(text, kb_context, loaded_pages, navigation)

        if review.get("coverage_status") == "need_more_pages":
            additional_refs = self._normalize_catalog_refs(
                review.get("additional_source_refs", []),
                kb_context,
                limit=MAX_REVIEW_ADDITIONS,
            )
            for ref in additional_refs:
                if ref not in selected_refs and len(selected_refs) < MAX_SELECTED_WIKI_PAGES:
                    selected_refs.append(ref)
            loaded_pages = self._load_catalog_pages(kb_context, selected_refs)

        engine_trace = {
            "navigation": navigation,
            "review": review,
            "selected_source_refs": selected_refs,
        }
        return loaded_pages or kb_context[:5], "llm_wiki_navigation_selected_pages", engine_trace

    def _plan_llm_wiki_navigation(self, text: str, kb_context: list[dict]) -> dict[str, Any]:
        system_prompt = (
            "You are navigating a small compiled LLM Wiki for a support agent. "
            "Use the wiki index and page cards to identify the minimal sufficient set of pages to read in full before answering. "
            "Return JSON only."
        )
        user_prompt = json.dumps(
            {
                "task": "Plan wiki navigation before any full-page reading.",
                "required_json_schema": {
                    "user_intent": "short string",
                    "information_needs": ["list of fact needs"],
                    "selected_source_refs": ["up to 3 source_ref strings"],
                    "reason": "short string",
                },
                "user_message": text,
                "rules": [
                    "Read the wiki index first, then page cards.",
                    "Select the smallest sufficient set of pages.",
                    "Cap the initial selection at 3 pages.",
                    "Prefer pages whose summaries or key facts directly cover the question.",
                ],
                "wiki_catalog": kb_context,
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
                "user_intent": text,
                "information_needs": [],
                "selected_source_refs": [],
                "reason": f"navigation_error:{type(exc).__name__}",
            }
        return parsed

    def _review_llm_wiki_coverage(
        self,
        text: str,
        kb_context: list[dict],
        loaded_pages: list[dict],
        navigation: dict[str, Any],
    ) -> dict[str, Any]:
        if not loaded_pages:
            return {
                "coverage_status": "need_more_pages",
                "additional_source_refs": [],
                "reason": "no_loaded_pages",
            }

        system_prompt = (
            "You are reviewing whether the currently selected full wiki pages are sufficient to answer the user safely. "
            "Use the catalog only to request additional pages if a specific fact is still missing. "
            "Return JSON only."
        )
        user_prompt = json.dumps(
            {
                "task": "Check whether the selected wiki pages are sufficient or whether up to 2 more pages must be read.",
                "required_json_schema": {
                    "coverage_status": "enough|need_more_pages",
                    "missing_facts": ["list of still-missing facts"],
                    "additional_source_refs": ["up to 2 source_ref strings"],
                    "reason": "short string",
                },
                "user_message": text,
                "navigation_plan": navigation,
                "selected_full_pages": loaded_pages,
                "wiki_catalog": kb_context,
                "rules": [
                    "Request extra pages only if a concrete answer-critical fact is still missing.",
                    "Do not ask for more than 2 extra pages.",
                    "If the selected pages already cover the answer, return coverage_status=enough.",
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
                "coverage_status": "enough",
                "missing_facts": [],
                "additional_source_refs": [],
                "reason": f"coverage_review_error:{type(exc).__name__}",
            }
        return parsed

    def _normalize_catalog_refs(self, refs: list[Any], kb_context: list[dict], *, limit: int) -> list[str]:
        cards_by_ref = {item.get("source_ref"): item for item in kb_context if item.get("source_ref")}
        normalized: list[str] = []
        for ref in refs:
            resolved = self._resolve_catalog_ref(str(ref), cards_by_ref)
            if resolved and resolved not in normalized:
                normalized.append(resolved)
            if len(normalized) >= limit:
                break
        return normalized

    def _resolve_catalog_ref(self, value: str, cards_by_ref: dict[str, dict]) -> str | None:
        if value in cards_by_ref:
            return value

        normalized_value = value.strip().lower()
        for source_ref, item in cards_by_ref.items():
            if not source_ref or source_ref.endswith("index.md"):
                continue
            page_title = str(item.get("page_title") or "").strip().lower()
            page_slug = Path(source_ref).stem.lower()
            if normalized_value in {page_title, page_slug}:
                return source_ref
        return None

    def _fallback_select_catalog_refs(self, text: str, kb_context: list[dict], *, limit: int) -> list[str]:
        query_terms = {token for token in text.lower().replace("-", " ").split() if len(token) >= 3}
        scored: list[tuple[int, str]] = []
        for item in kb_context:
            source_ref = item.get("source_ref")
            if not source_ref or source_ref.endswith("index.md"):
                continue
            haystack = " ".join(
                [
                    str(item.get("page_title") or ""),
                    str(item.get("page_summary") or ""),
                    str(item.get("page_preview") or ""),
                    str(item.get("text") or ""),
                ]
            ).lower()
            score = sum(1 for term in query_terms if term in haystack)
            if score:
                scored.append((score, source_ref))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [source_ref for _, source_ref in scored[:limit]]

    def _load_catalog_pages(self, kb_context: list[dict], selected_refs: list[str]) -> list[dict]:
        cards_by_ref = {item.get("source_ref"): item for item in kb_context if item.get("source_ref")}
        loaded: list[dict] = []
        for ref in selected_refs[:MAX_SELECTED_WIKI_PAGES]:
            path = Path(ref)
            if not path.exists() or not path.is_file():
                continue
            raw_text = path.read_text(encoding="utf-8")
            body = self._strip_frontmatter(raw_text).strip()
            card = cards_by_ref.get(ref, {})
            loaded.append(
                {
                    "source_ref": ref,
                    "source_type": "wiki_page",
                    "page_title": card.get("page_title") or path.stem,
                    "linked_pages": card.get("linked_pages", []),
                    "retrieval_notes": "llm-wiki selected full page after navigation",
                    "text": body,
                }
            )
        return loaded

    def _strip_frontmatter(self, text: str) -> str:
        if not text.startswith("---\n"):
            return text
        parts = text.split("\n---\n", 1)
        if len(parts) == 2:
            return parts[1]
        return text

    def _extract_source_refs(self, kb_context: list[dict]) -> list[str]:
        refs: list[str] = []
        for item in kb_context:
            ref = item.get("source_ref")
            if ref and ref not in refs:
                refs.append(ref)
        return refs

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
