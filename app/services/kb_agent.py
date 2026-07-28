from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.integrations.llm.base import BaseLLMClient
from app.integrations.llm.factory import get_llm_client
from app.services.system_prompt import KBAgentPromptService

MAX_CATALOG_SELECTION = 3
MAX_REVIEW_ADDITIONS = 2
MAX_SELECTED_WIKI_PAGES = 5
MAX_GROUNDED_FACTS = 8
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


class KBAgentService:
    def __init__(self, client: BaseLLMClient | None = None, prompt_service: KBAgentPromptService | None = None) -> None:
        self._client_injected = client is not None
        self.client = client or get_llm_client(
            provider=settings.kb_agent_provider,
            base_url=settings.kb_agent_base_url,
            api_key=settings.kb_agent_api_key,
            api_keys=settings.kb_agent_api_key_list,
            model=settings.kb_agent_model,
            timeout_seconds=settings.kb_agent_timeout_seconds,
            max_retries=settings.kb_agent_max_retries,
            retry_backoff_seconds=settings.kb_agent_retry_backoff_seconds,
            retry_deadline_seconds=settings.kb_agent_retry_deadline_seconds,
        )
        self.temperature = settings.kb_agent_temperature
        self.prompt_service = prompt_service or KBAgentPromptService()

    def _reset_llm_trace(self) -> None:
        self._active_llm_trace: list[dict[str, Any]] = []

    def _record_llm_call(self, step: str) -> None:
        info = self.client.get_last_call_info() if hasattr(self.client, 'get_last_call_info') else {}
        if not info:
            return
        usage = info.get('usage') or {}
        self._active_llm_trace.append({
            'role': 'kb_agent',
            'step': step,
            'provider': info.get('provider'),
            'model': info.get('model'),
            'duration_ms': info.get('duration_ms'),
            'attempts': info.get('attempts'),
            'api_key_index': info.get('api_key_index'),
            'used_failover': info.get('used_failover'),
            'failover_count': info.get('failover_count'),
            'failover_events': info.get('failover_events') or [],
            'usage': {
                'prompt_tokens': usage.get('prompt_tokens'),
                'completion_tokens': usage.get('completion_tokens'),
                'total_tokens': usage.get('total_tokens'),
            },
            'error': info.get('error'),
        })

    @staticmethod
    def _parse_json_response(raw: str) -> dict[str, Any]:
        text = str(raw or '').strip()
        if not text:
            raise json.JSONDecodeError('empty response', text, 0)

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed

        fenced = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, flags=re.DOTALL | re.IGNORECASE)
        candidates = [fenced.group(1)] if fenced else []

        object_start = text.find('{')
        object_end = text.rfind('}')
        if object_start != -1 and object_end != -1 and object_end > object_start:
            candidates.append(text[object_start:object_end + 1])

        array_start = text.find('[')
        array_end = text.rfind(']')
        if array_start != -1 and array_end != -1 and array_end > array_start:
            candidates.append(text[array_start:array_end + 1])

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed

        raise json.JSONDecodeError('unable to recover JSON object', text, 0)

    @staticmethod
    def _tokenize_query(text: str) -> list[str]:
        return [token for token in re.findall(r"[\\w\\-]+", str(text or "").lower()) if len(token) >= 3]

    def _build_navigation_query(self, text: str, conversation_context: dict | None = None) -> str:
        if not isinstance(conversation_context, dict):
            return text
        recent_messages = conversation_context.get("recent_messages", [])
        if not isinstance(recent_messages, list):
            return text
        recent_messages = [item for item in recent_messages if isinstance(item, dict)]
        if not recent_messages:
            return text

        parts = [str(text or "").strip()]
        for item in recent_messages[-4:]:
            role = str(item.get("role") or "").strip().lower()
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            prefix = "assistant" if role == "assistant" else "user"
            parts.append(f"{prefix} {content}")
        return "\n".join(part for part in parts if part)

    def _deterministic_navigation(
        self,
        text: str,
        kb_context: list[dict],
        *,
        conversation_context: dict | None = None,
    ) -> dict[str, Any]:
        query = self._build_navigation_query(text, conversation_context=conversation_context)
        current_terms = self._tokenize_query(text)
        expanded_terms = self._tokenize_query(query)
        context_terms = [term for term in expanded_terms if term not in current_terms]

        scored: list[tuple[int, str]] = []
        for item in kb_context:
            source_ref = item.get("source_ref")
            if not source_ref or str(source_ref).endswith("index.md"):
                continue
            haystack = " ".join(
                [
                    str(item.get("page_title") or ""),
                    str(item.get("page_summary") or ""),
                    str(item.get("page_preview") or ""),
                    str(item.get("text") or ""),
                    " ".join(str(link) for link in item.get("linked_pages", []) if link),
                ]
            ).lower()
            score = sum(5 for term in current_terms if term in haystack)
            score += sum(2 for term in context_terms if term in haystack)
            if score:
                scored.append((score, str(source_ref)))

        scored.sort(key=lambda item: (-item[0], item[1]))
        selected_refs = [source_ref for _, source_ref in scored[:MAX_CATALOG_SELECTION - 1]]
        cards_by_ref = {str(item.get("source_ref")): item for item in kb_context if item.get("source_ref")}
        for source_ref in list(selected_refs):
            card = cards_by_ref.get(source_ref, {})
            for link in card.get("linked_pages", []):
                resolved = self._resolve_catalog_ref(str(link), cards_by_ref)
                if resolved and resolved not in selected_refs:
                    selected_refs.append(resolved)
                if len(selected_refs) >= MAX_CATALOG_SELECTION:
                    break
            if len(selected_refs) >= MAX_CATALOG_SELECTION:
                break
        for _, source_ref in scored:
            if source_ref not in selected_refs:
                selected_refs.append(source_ref)
            if len(selected_refs) >= MAX_CATALOG_SELECTION:
                break
        if not selected_refs:
            selected_refs = self._fallback_select_catalog_refs(query, kb_context, limit=MAX_CATALOG_SELECTION)
            selected_reason = "deterministic_navigation:fallback_lexical"
        else:
            selected_reason = "deterministic_navigation:current_turn_weighted_link_expansion"

        return {
            "user_intent": text,
            "information_needs": [],
            "selected_source_refs": selected_refs,
            "reason": selected_reason,
        }

    def read(
        self,
        text: str,
        kb_hits: list[dict],
        *,
        conversation_context: dict | None = None,
    ) -> dict[str, Any]:
        self._reset_llm_trace()
        if not kb_hits:
            return {
                "kb_status": "not_found",
                "kb_mode": "empty",
                "grounding_status": "not_found",
                "answer_context": [],
                "grounded_facts": [],
                "answer_basis": "",
                "source_refs": [],
                "trace": {"reason": "no_kb_hits"},
            }

        kb_context, kb_mode = self._prepare_kb_context(kb_hits)
        trace: dict[str, Any] = {"kb_mode": kb_mode}
        answer_context = kb_context
        answer_mode = kb_mode

        if kb_mode == "llm_wiki_catalog":
            if settings.kb_agent_deterministic_navigation:
                navigation = self._deterministic_navigation(text, kb_context, conversation_context=conversation_context)
            else:
                navigation = self._plan_navigation(text, kb_context, conversation_context=conversation_context)
            selected_refs = self._normalize_catalog_refs(
                navigation.get("selected_source_refs", []),
                kb_context,
                limit=MAX_CATALOG_SELECTION,
            )
            if not selected_refs:
                selected_refs = self._fallback_select_catalog_refs(text, kb_context, limit=MAX_CATALOG_SELECTION)
            loaded_pages = self._load_catalog_pages(kb_context, selected_refs)
            if settings.kb_agent_skip_coverage_review:
                review = {
                    "coverage_status": "enough",
                    "missing_facts": [],
                    "additional_source_refs": [],
                    "reason": "skip_coverage_review:settings",
                }
            else:
                review = self._review_coverage(
                    text,
                    kb_context,
                    loaded_pages,
                    navigation,
                    conversation_context=conversation_context,
                )
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
            answer_context = loaded_pages or kb_context[:5]
            answer_mode = "llm_wiki_selected_pages"
            trace.update(
                {
                    "navigation": navigation,
                    "review": review,
                    "selected_source_refs": selected_refs,
                }
            )

        extraction = self._extract_grounded_facts(
            text,
            answer_context,
            answer_mode=answer_mode,
            conversation_context=conversation_context,
        )
        source_refs = extraction.get("cited_source_refs") or self._extract_source_refs(answer_context)
        return {
            "kb_status": "found",
            "kb_mode": answer_mode,
            "grounding_status": extraction.get("grounding_status", "not_found"),
            "answer_context": answer_context,
            "grounded_facts": extraction.get("grounded_facts", []),
            "answer_basis": str(extraction.get("answer_basis") or "").strip(),
            "missing_information": extraction.get("missing_information", []),
            "source_refs": source_refs,
            "trace": trace | {"extraction": extraction, "llm_trace": list(self._active_llm_trace)},
        }

    def _plan_navigation(
        self,
        text: str,
        kb_context: list[dict],
        *,
        conversation_context: dict | None = None,
    ) -> dict[str, Any]:
        if settings.kb_agent_provider == "stub" and not self._client_injected:
            return {
                "user_intent": text,
                "information_needs": [],
                "selected_source_refs": self._fallback_select_catalog_refs(text, kb_context, limit=MAX_CATALOG_SELECTION),
                "reason": "stub_navigation",
            }

        system_prompt = self.prompt_service.load_system_prompt()
        user_prompt = json.dumps(
            {
                "task": "Wiki navigation plan before full-page reading.",
                "required_json_schema": {
                    "user_intent": "short string",
                    "information_needs": ["list of fact needs"],
                    "selected_source_refs": ["up to 3 source_ref strings"],
                    "reason": "short string",
                },
                "user_message": text,
                "conversation_context": conversation_context or {},
                "rules": [
                    "Сначала прочитай wiki index и page cards, а не полные страницы.",
                    "Выбери минимально достаточный стартовый набор страниц для полного чтения.",
                    "Не выбирай больше 3 страниц на первом шаге.",
                    "Не отвечай на вопрос пользователя на этом шаге.",
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
            parsed = self._parse_json_response(raw)
            self._record_llm_call('navigation')
            return parsed
        except Exception as exc:
            self._record_llm_call('navigation')
            return {
                "user_intent": text,
                "information_needs": [],
                "selected_source_refs": self._fallback_select_catalog_refs(text, kb_context, limit=MAX_CATALOG_SELECTION),
                "reason": f"navigation_error:{type(exc).__name__}",
            }

    def _review_coverage(
        self,
        text: str,
        kb_context: list[dict],
        loaded_pages: list[dict],
        navigation: dict[str, Any],
        *,
        conversation_context: dict | None = None,
    ) -> dict[str, Any]:
        if not loaded_pages:
            return {
                "coverage_status": "need_more_pages",
                "missing_facts": ["no pages loaded"],
                "additional_source_refs": [],
                "reason": "no_loaded_pages",
            }
        if settings.kb_agent_provider == "stub" and not self._client_injected:
            return {
                "coverage_status": "enough",
                "missing_facts": [],
                "additional_source_refs": [],
                "reason": "stub_review",
            }

        system_prompt = self.prompt_service.load_system_prompt()
        user_prompt = json.dumps(
            {
                "task": "Coverage review after selective full-page reading.",
                "required_json_schema": {
                    "coverage_status": "enough|need_more_pages",
                    "missing_facts": ["list of still-missing facts"],
                    "additional_source_refs": ["up to 2 source_ref strings"],
                    "reason": "short string",
                },
                "user_message": text,
                "conversation_context": conversation_context or {},
                "navigation_plan": navigation,
                "selected_full_pages": loaded_pages,
                "wiki_catalog": kb_context,
                "rules": [
                    "Запрашивай дополнительные страницы только если действительно не хватает критичного факта.",
                    "Не запрашивай больше 2 дополнительных страниц.",
                    "Если текущих страниц достаточно, верни coverage_status=enough.",
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
            parsed = self._parse_json_response(raw)
            self._record_llm_call('coverage_review')
            return parsed
        except Exception as exc:
            self._record_llm_call('coverage_review')
            return {
                "coverage_status": "enough",
                "missing_facts": [],
                "additional_source_refs": [],
                "reason": f"coverage_review_error:{type(exc).__name__}",
            }

    def _extract_grounded_facts(
        self,
        text: str,
        answer_context: list[dict],
        *,
        answer_mode: str,
        conversation_context: dict | None = None,
    ) -> dict[str, Any]:
        if not answer_context:
            return {
                "grounding_status": "not_found",
                "answer_basis": "",
                "grounded_facts": [],
                "missing_information": [],
                "cited_source_refs": [],
                "reason": "empty_answer_context",
            }
        if settings.kb_agent_provider == "stub" and not self._client_injected:
            return self._fallback_grounded_facts(answer_context, reason="stub_grounding")

        system_prompt = self.prompt_service.load_system_prompt()
        minimal_schema = settings.kb_agent_minimal_extraction_schema
        required_json_schema = {
            "grounding_status": "ready|not_found",
            "answer_basis": "short factual synthesis for the support agent, not a customer reply",
            "grounded_facts": ["bullet-sized verified facts"],
            "cited_source_refs": ["source_ref strings used"],
            "reason": "short string",
        }
        if not minimal_schema:
            required_json_schema["missing_information"] = ["facts that remain unknown"]
        rules = [
            "Работай как KB agent, а не как клиентский консультант.",
            "Извлекай только подтвержденные факты из предоставленных страниц wiki.",
            "Не дополняй выводы догадками и не отвечай в клиентском стиле.",
            "Явное общее правило из страницы можно считать подтверждённым для частного случая только когда его формулировка прямо охватывает все или остальные категории; процитируй это правило как факт и укажи страницу.",
            "answer_basis должен быть короткой служебной опорой для финального support-agent ответа.",
        ]
        if minimal_schema:
            rules.extend(
                [
                    "Верни минимальный JSON: grounding_status, answer_basis, grounded_facts, cited_source_refs, reason.",
                    "Не добавляй missing_information, если можно безопасно ответить без него.",
                ]
            )
        else:
            rules.append("Если данных не хватает, явно перечисли чего не хватает в missing_information.")

        user_prompt = json.dumps(
            {
                "task": "Extract grounded facts from the selected wiki pages for the support agent.",
                "required_json_schema": required_json_schema,
                "user_message": text,
                "conversation_context": conversation_context or {},
                "kb_mode": answer_mode,
                "rules": rules,
                "selected_full_pages": answer_context,
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
            parsed = self._parse_json_response(raw)
            self._record_llm_call('grounded_extraction')
        except Exception as exc:
            self._record_llm_call('grounded_extraction')
            return {
                "grounding_status": "retry_pending",
                "answer_basis": "",
                "grounded_facts": [],
                "missing_information": [],
                "cited_source_refs": [],
                "reason": f"grounding_error:{type(exc).__name__}",
            }

        if not parsed.get("cited_source_refs"):
            parsed["cited_source_refs"] = self._extract_source_refs(answer_context)
        if not isinstance(parsed.get("missing_information"), list):
            parsed["missing_information"] = []
        if not isinstance(parsed.get("answer_basis"), str):
            parsed["answer_basis"] = str(parsed.get("answer_basis") or "").strip()
        grounded_facts = parsed.get("grounded_facts") or []
        if isinstance(grounded_facts, list):
            parsed["grounded_facts"] = [str(item).strip() for item in grounded_facts if str(item).strip()][:MAX_GROUNDED_FACTS]
        else:
            parsed["grounded_facts"] = []
        return parsed

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
                    "retrieval_notes": "kb-agent selected full page after wiki navigation",
                    "text": body,
                }
            )
        return loaded

    def _fallback_grounded_facts(self, answer_context: list[dict], *, reason: str) -> dict[str, Any]:
        facts: list[str] = []
        for item in answer_context:
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            for sentence in SENTENCE_SPLIT_RE.split(text.replace("\n", " ")):
                cleaned = sentence.strip()
                if cleaned and cleaned not in facts:
                    facts.append(cleaned)
                if len(facts) >= MAX_GROUNDED_FACTS:
                    break
            if len(facts) >= MAX_GROUNDED_FACTS:
                break
        return {
            "grounding_status": "ready" if facts else "not_found",
            "answer_basis": " ".join(facts[:3]).strip(),
            "grounded_facts": facts[:MAX_GROUNDED_FACTS],
            "missing_information": [],
            "cited_source_refs": self._extract_source_refs(answer_context),
            "reason": reason,
        }

    def _extract_source_refs(self, kb_context: list[dict]) -> list[str]:
        refs: list[str] = []
        for item in kb_context:
            ref = item.get("source_ref")
            if ref and ref not in refs:
                refs.append(ref)
        return refs

    def _strip_frontmatter(self, text: str) -> str:
        if not text.startswith("---\n"):
            return text
        parts = text.split("\n---\n", 1)
        if len(parts) == 2:
            return parts[1]
        return text
