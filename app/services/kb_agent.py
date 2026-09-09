from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.integrations.llm.base import BaseLLMClient
from app.integrations.llm.factory import get_llm_client
from app.integrations.llm.openai_compatible import LLMRecoveryExhausted
from app.services.answer_evidence import (
    EvidenceValidationError,
    build_answer_evidence,
    empty_answer_evidence,
    validate_answer_evidence,
)
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
            drop_params=settings.openai_compatible_drop_params,
        )
        self.temperature = settings.kb_agent_temperature
        self.prompt_service = prompt_service or KBAgentPromptService()
        self._reset_llm_trace()

    def _reset_llm_trace(self) -> None:
        self._active_llm_trace: list[dict[str, Any]] = []

    def _record_llm_call(self, step: str) -> None:
        info = self.client.get_last_call_info() if hasattr(self.client, 'get_last_call_info') else {}
        info = info or {}
        usage = info.get('usage') or {}
        self._active_llm_trace.append({
            'entry_kind': 'model_call',
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
        require_coverage_review: bool = False,
    ) -> dict[str, Any]:
        self._reset_llm_trace()
        if not kb_hits:
            empty = empty_answer_evidence(text)
            return {
                "kb_status": "not_found",
                "kb_mode": "empty",
                "grounding_status": "not_found",
                "answer_context": [],
                "grounded_facts": [],
                "answer_basis": "",
                "source_refs": [],
                "answer_evidence": empty,
                "trace": {"reason": "no_kb_hits", "answer_evidence": empty},
            }

        kb_context, kb_mode = self._prepare_kb_context(kb_hits)
        trace: dict[str, Any] = {"kb_mode": kb_mode}
        if any(item.get("kb_architecture") == "llm_wiki" for item in kb_context):
            trace.update(
                {
                    "kb_architecture": "llm_wiki",
                    "navigation_mode": "llm",
                    "coverage_review_mode": "llm",
                    "extraction_mode": "grounded",
                }
            )
        answer_context = kb_context
        answer_mode = kb_mode

        if kb_mode == "llm_wiki_catalog":
            is_compiled_llm_wiki = trace.get("kb_architecture") == "llm_wiki"
            if settings.kb_agent_deterministic_navigation and not is_compiled_llm_wiki:
                navigation = self._deterministic_navigation(text, kb_context, conversation_context=conversation_context)
            else:
                navigation = self._plan_navigation(text, kb_context, conversation_context=conversation_context)
            selected_refs = self._normalize_catalog_refs(
                navigation.get("selected_source_refs", []),
                kb_context,
                limit=MAX_CATALOG_SELECTION,
            )
            if not selected_refs:
                if trace.get("kb_architecture") == "llm_wiki":
                    if navigation.get("_navigation_succeeded"):
                        return {
                            "kb_status": "found",
                            "kb_mode": "llm_wiki_selected_pages",
                            "grounding_status": "not_found",
                            "answer_context": [],
                            "grounded_facts": [],
                            "answer_basis": "",
                            "missing_information": [],
                            "source_refs": [],
                            "reason": "navigation_selected_no_pages",
                            "trace": trace | {
                                "navigation": navigation,
                                "review": {"coverage_status": "not_run", "reason": "navigation_selected_no_pages"},
                                "selected_source_refs": [],
                                "llm_trace": list(self._active_llm_trace),
                            },
                        }
                    return self._retry_pending_catalog_read(trace, navigation, "navigation_unavailable")
                selected_refs = self._fallback_select_catalog_refs(text, kb_context, limit=MAX_CATALOG_SELECTION)
            loaded_pages = self._load_catalog_pages(kb_context, selected_refs)
            if settings.kb_agent_merge_coverage_extraction:
                # The extractor already returns typed per-question coverage bound to
                # cited facts.  Avoid a second LLM pass that merely pre-checks it.
                review = {
                    "coverage_status": "merged_into_extraction",
                    "missing_facts": [],
                    "additional_source_refs": [],
                    "reason": "merged_into_grounded_extraction",
                }
            elif settings.kb_agent_skip_coverage_review and not require_coverage_review:
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
            if trace.get("kb_architecture") == "llm_wiki" and review.get("coverage_status") == "error":
                return self._retry_pending_catalog_read(trace, navigation, "coverage_review_unavailable", review=review)
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
        source_refs = extraction.get("cited_source_refs", [])
        answer_evidence = extraction.get("answer_evidence") or empty_answer_evidence(text, context_scope=self._tool_request(conversation_context).get("context_scope", ""), acquisition_status=extraction.get("grounding_status", "not_found"))
        return {
            "kb_status": "found",
            "kb_mode": answer_mode,
            "grounding_status": extraction.get("grounding_status", "not_found"),
            "answer_context": answer_context,
            "grounded_facts": extraction.get("grounded_facts", []),
            "answer_basis": str(extraction.get("answer_basis") or "").strip(),
            "missing_information": extraction.get("missing_information", []),
            "source_refs": source_refs,
            "answer_evidence": answer_evidence,
            "reason": extraction.get("reason", ""),
            "trace": trace | {"extraction": extraction, "llm_trace": list(self._active_llm_trace)},
        }

    def _retry_pending_catalog_read(
        self,
        trace: dict[str, Any],
        navigation: dict[str, Any],
        reason: str,
        *,
        review: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        recovery_exhausted = any(
            "LLMRecoveryExhausted" in str(value)
            for value in (
                reason,
                navigation.get("reason"),
                (review or {}).get("reason"),
            )
        )
        return {
            "kb_status": "found",
            "kb_mode": "llm_wiki_selected_pages",
            "grounding_status": "llm_unavailable" if recovery_exhausted else "retry_pending",
            "answer_context": [],
            "grounded_facts": [],
            "answer_basis": "",
            "missing_information": [],
            "source_refs": [],
            "answer_evidence": empty_answer_evidence("", acquisition_status="unavailable"),
            "reason": "llm_recovery_exhausted:LLMRecoveryExhausted" if recovery_exhausted else reason,
            "trace": trace
            | {
                "navigation": navigation,
                "review": review or {"coverage_status": "not_run", "reason": reason},
                "selected_source_refs": [],
                "extraction": {"reason": reason},
                "llm_trace": list(self._active_llm_trace),
            },
        }

    @staticmethod
    def _tool_request(conversation_context: dict | None) -> dict[str, str]:
        if not isinstance(conversation_context, dict):
            return {}
        request = conversation_context.get("tool_request")
        if not isinstance(request, dict):
            return {}
        return {key: str(request.get(key) or "").strip() for key in ("query", "context_scope", "needed_fact")}

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
                "tool_request": self._tool_request(conversation_context),
                "conversation_context": conversation_context or {},
                "rules": [
                    "Tool request is a binding scope contract selected by the customer-turn model, not advisory context.",
                    "Choose pages only for tool_request.query and tool_request.needed_fact within tool_request.context_scope.",
                    "Do not widen context_scope to a parent category and do not select pages solely for alternatives excluded by that scope.",
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
            parsed["_navigation_succeeded"] = True
            self._record_llm_call('navigation')
            return parsed
        except Exception as exc:
            self._record_llm_call('navigation')
            return {
                "user_intent": text,
                "information_needs": [],
                "selected_source_refs": [],
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
                "tool_request": self._tool_request(conversation_context),
                "conversation_context": conversation_context or {},
                "navigation_plan": navigation,
                "selected_full_pages": loaded_pages,
                "wiki_catalog": kb_context,
                "rules": [
                    "Tool request is a binding scope contract selected by the customer-turn model, not advisory context.",
                    "Assess coverage only for tool_request.needed_fact within tool_request.context_scope; do not request or retain material for excluded alternatives.",
                    "Проверь покрытие каждой самостоятельной практической части текущего сообщения; если выбранные страницы отвечают только на часть запроса, запроси страницы для остальных частей.",
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
                "coverage_status": "error",
                "missing_facts": [],
                "additional_source_refs": [],
                "reason": f"coverage_review_error:{type(exc).__name__}",
            }

    @staticmethod
    def _compact_facts_to_coverage(facts: object, coverage: object) -> object:
        """Drop surplus extraction facts that do not support an answered part.

        The evidence budget is a transport boundary, not a reason to discard a
        directly covered answer.  Never choose facts by topic or text: retain
        only IDs explicitly cited by the extraction model's coverage mapping.
        If that mapping itself needs more than the budget, validation still
        fails closed.
        """
        if not isinstance(facts, list) or len(facts) <= MAX_GROUNDED_FACTS or not isinstance(coverage, dict):
            return facts
        answered_parts = coverage.get("answered_parts")
        if not isinstance(answered_parts, list):
            return facts
        required_ids: set[str] = set()
        for part in answered_parts:
            if not isinstance(part, dict) or not isinstance(part.get("fact_ids"), list):
                return facts
            if any(not isinstance(fact_id, str) for fact_id in part["fact_ids"]):
                return facts
            required_ids.update(part["fact_ids"])
        compacted = [fact for fact in facts if isinstance(fact, dict) and fact.get("id") in required_ids]
        if not required_ids:
            return []
        return compacted if len(compacted) <= MAX_GROUNDED_FACTS else facts

    def _extract_grounded_facts(
        self,
        text: str,
        answer_context: list[dict],
        *,
        answer_mode: str,
        conversation_context: dict | None = None,
    ) -> dict[str, Any]:
        tool_request = self._tool_request(conversation_context)
        original_question = str((conversation_context or {}).get("user_question") or text)
        empty = empty_answer_evidence(original_question, context_scope=tool_request.get("context_scope", ""))
        if not answer_context:
            return {
                "grounding_status": "not_found",
                "answer_basis": "",
                "grounded_facts": [],
                "missing_information": [],
                "cited_source_refs": [],
                "answer_evidence": empty,
                "reason": "empty_answer_context",
            }
        if settings.kb_agent_provider == "stub" and not self._client_injected:
            return self._fallback_grounded_facts(answer_context, reason="stub_grounding", user_question=original_question, context_scope=tool_request.get("context_scope", ""))

        system_prompt = self.prompt_service.load_system_prompt()
        minimal_schema = settings.kb_agent_minimal_extraction_schema
        required_json_schema = {
            "grounding_status": "ready|not_found",
            "needs_customer_clarification": "boolean: true только для неоднозначности, которую может устранить клиент; отсутствие знания — false",
            "answer_basis": "Кандидатная краткая сводка только приведённых фактов, не самостоятельный источник знания и не клиентский ответ",
            "grounded_facts": [
                {
                    "id": "Уникальный локальный ID факта",
                    "text": "Подтверждённое утверждение без усиления смысла",
                    "source_refs": [
                        "Точные source_ref выбранных страниц, подтверждающих этот факт"
                    ],
                    "conditions": [
                        "Условия из источника; пустой список, если они не указаны"
                    ],
                    "modality": "Формулировка модальности из источника или null, если она не указана"
                }
            ],
            "coverage": {
                "status": "full|partial|none|ambiguous|conflicting",
                "answered_parts": [
                    {
                        "question_part": "Часть текущего вопроса с прямым подтверждённым ответом",
                        "fact_ids": [
                            "ID фактов, прямо отвечающих на эту часть"
                        ]
                    }
                ],
                "missing_parts": [
                    "Части вопроса без прямого ответа"
                ],
                "conflicts": [
                    {
                        "fact_ids": [
                            "ID конфликтующих фактов"
                        ],
                        "description": "Неразрешённое противоречие без собственного разрешения"
                    }
                ],
                "unresolved_constraints": [
                    "Выбранные клиентом ограничения, применимость которых не подтверждена"
                ]
            },
            "cited_source_refs": [
                "Точные source_ref, использованные в фактах"
            ],
            "reason": "Краткое объяснение результата"
        }
        if not minimal_schema:
            required_json_schema["missing_information"] = ["Факты, которые остаются неизвестными"]
        rules = [
            "tool_request задаёт цель поиска, но не является источником фактов или полномочием изменить предмет вопроса. При расхождении с исходным user_message и явным контекстом клиента сохраняй исходный предмет и ограничения клиента.",
            "Извлекай факты для ответа на исходный вопрос клиента. Используй tool_request.needed_fact и tool_request.context_scope только в той части, которая соответствует этому вопросу. Не добавляй сведения о другом варианте или более широкой категории, если клиент явно не просит сравнение или смену предмета.",
            "Если выбранная страница содержит исключённые альтернативы, полностью исключи их факты из grounded_facts и answer_basis.",
            "Не дополняй выводы догадками и не отвечай в клиентском стиле.",
            "Каждый факт оформляй отдельным объектом с уникальным локальным id, текстом, точными source_refs, условиями и модальностью из источника. Не дополняй отсутствующие условия и модальность; не указывай страницу, которая не подтверждает этот факт.",
            "Верни не более 8 объектов grounded_facts. Если страниц дают больше связанных сведений, выбери минимальный набор фактов, прямо нужный для coverage исходного вопроса; не добавляй запасные или тематически соседние факты.",
            "В coverage связывай прямо отвеченные части исходного вопроса с ID фактов, подтверждающих именно запрошенное отношение, а не отдельные слова или общую тему. Если ни одна часть фактического запроса не имеет прямого ответа, укажи status=none и пустой answered_parts. Если прямой ответ есть только на часть запроса, укажи status=partial и перечисли остальные части в missing_parts. Ставь status=full только при прямом покрытии всего фактического запроса с его существенными условиями. Конфликты и неоднозначность отражай предусмотренными схемой состояниями без собственного разрешения.",
            "Отсутствие упоминания — отсутствие знания, а не отрицательный факт. Подтверждённое отрицание допустимо только когда оно прямо содержится в источнике.",
            "answer_basis — кандидатная сводка фактов, а не отдельное доказательство. Не добавляй в неё утверждения или отношения, которых нет в фактах. Конфликты не разрешай догадкой.",
            "Сохраняй существенные условия и точную модальность. При частичном покрытии сохрани подтверждённые факты для учёта evidence, но не объявляй весь запрос отвеченным и не достраивай неизвестное. full допустим только при ready, непустых фактах и answered_parts, пустых missing_parts, unresolved_constraints и conflicts. ambiguous означает неоднозначность запроса, которую действительно может устранить клиент; отсутствие знания не является такой неоднозначностью.",
            "Явное общее правило из страницы можно считать подтверждённым для частного случая только когда его формулировка прямо охватывает все или остальные категории; процитируй это правило как факт и укажи страницу.",
            "answer_basis должен быть короткой служебной опорой для финального support-agent ответа.",
            "Разрешай короткие и неполные продолжения по явному контексту диалога клиента. Сохраняй выбранный клиентом предмет; не восстанавливай его из неподтверждённых предположений tool_request.",
            "Для продолжения с выбранным клиентом предметом извлекай относящиеся к нему требования. Не добавляй факты о соседних, альтернативных, прежних, более широких или последующих предметах.",
            "Последнее явное ограничение или предпочтение устанавливай по сообщению и диалогу клиента, а не по предположению в tool_request.context_scope. Исключённые альтернативы не должны появляться в grounded_facts или answer_basis, если клиент прямо не просит сравнение или не меняет ограничение.",
            "Если страница прямо указывает авторитетный источник запрошенного изменяющегося значения, сохрани этот точный источник как факт. Отдельно укажи, какую часть вопроса он прямо покрывает; не выдумывай само значение, которого нет в выбранных страницах.",
            "Наличие тематически связанных фактов и ссылок само по себе не доказывает прямого покрытия вопроса. ready означает успешное получение данных; прямое покрытие отражается отдельно в coverage. Если прямого знания нет, допускается not_found при наличии связанных фактов.",
        ]
        if minimal_schema:
            rules.extend(
                [
                    "Верни JSON: grounding_status, needs_customer_clarification, answer_basis, grounded_facts, coverage, cited_source_refs, reason.",
                    "needs_customer_clarification=true ставь только если вопрос клиента неоднозначен и уточнение клиента может исправить это; отсутствие факта в wiki не является уточнением клиента.",
                    "Не добавляй missing_information, если можно безопасно ответить без него.",
                ]
            )
        else:
            rules.append("Если данных не хватает, явно перечисли чего не хватает в missing_information и укажи needs_customer_clarification=true только если это может уточнить сам клиент; отсутствие бизнес-факта в wiki — false.")

        user_prompt = json.dumps({
            "task": "Проверь прямое покрытие исходного user_message с учётом явно заданного клиентом контекста выбранными страницами. Для каждой фактической части запроса установи, подтверждают ли страницы именно запрошенное утверждение или отношение с его существенными ограничениями. Совпадение темы не является ответом. Извлеки подтверждённые сведения без новых связей между ними и отдельно заполни coverage. Поисковая формулировка tool_request не доказывает фактов и не заменяет вопрос клиента.",
            "required_json_schema": required_json_schema,
            "user_message": original_question,
            "tool_request": tool_request,
            "conversation_context": conversation_context or {},
            "kb_mode": answer_mode,
            "rules": rules,
            "selected_full_pages": answer_context,
        }, ensure_ascii=False)
        try:
            raw = self.client.generate(system_prompt=system_prompt, user_prompt=user_prompt, temperature=self.temperature, response_format={"type": "json_object"})
            parsed = self._parse_json_response(raw)
            self._record_llm_call('grounded_extraction')
        except LLMRecoveryExhausted as exc:
            self._record_llm_call('grounded_extraction')
            packet = empty_answer_evidence(original_question, context_scope=tool_request.get("context_scope", ""), acquisition_status="unavailable")
            reason = f"llm_recovery_exhausted:{type(exc).__name__}"
            return {"grounding_status": "llm_unavailable", "answer_basis": "", "grounded_facts": [], "missing_information": [], "cited_source_refs": [], "answer_evidence": packet, "reason": reason}
        except Exception as exc:
            self._record_llm_call('grounded_extraction')
            packet = empty_answer_evidence(original_question, context_scope=tool_request.get("context_scope", ""), acquisition_status="unavailable")
            reason = f"grounding_error:{type(exc).__name__}"
            return {"grounding_status": "retry_pending", "answer_basis": "", "grounded_facts": [], "missing_information": [], "cited_source_refs": [], "answer_evidence": packet, "reason": reason}

        selected_refs = self._extract_source_refs(answer_context)
        try:
            required = {"grounding_status", "needs_customer_clarification", "answer_basis",
                        "grounded_facts", "coverage", "cited_source_refs", "reason"}
            if not isinstance(parsed, dict) or not required.issubset(parsed):
                raise EvidenceValidationError("evidence_fields_missing")
            if type(parsed["needs_customer_clarification"]) is not bool or not isinstance(parsed["reason"], str):
                raise EvidenceValidationError("evidence_field_type_invalid")
            refs = parsed["cited_source_refs"]
            if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in selected_refs for ref in refs):
                raise EvidenceValidationError("evidence_source_ref_invalid")
            missing = parsed.get("missing_information", [])
            if not isinstance(missing, list) or any(not isinstance(part, str) for part in missing):
                raise EvidenceValidationError("evidence_field_type_invalid")
            answer_evidence = build_answer_evidence({
                "schema_version": "answer-evidence/v1", "user_question": original_question,
                "context_scope": tool_request.get("context_scope", ""),
                "acquisition_status": parsed["grounding_status"],
                "facts": self._compact_facts_to_coverage(parsed["grounded_facts"], parsed["coverage"]),
                "coverage": parsed["coverage"], "answer_basis": parsed["answer_basis"],
                "calendar_facts": [], "policy_evidence": [],
            }, selected_source_refs=selected_refs)
            if any(ref not in refs for fact in answer_evidence["facts"] for ref in fact["source_refs"]):
                raise EvidenceValidationError("evidence_citation_missing")
        except EvidenceValidationError as exc:
            packet = empty_answer_evidence(original_question, context_scope=tool_request.get("context_scope", ""), acquisition_status="unavailable")
            return {"grounding_status": "unavailable", "answer_basis": "", "grounded_facts": [],
                    "missing_information": [], "cited_source_refs": [], "needs_customer_clarification": False,
                    "answer_evidence": packet, "reason": str(exc)}
        return {"grounding_status": parsed["grounding_status"], "answer_basis": answer_evidence["answer_basis"],
                "grounded_facts": answer_evidence["facts"], "coverage": answer_evidence["coverage"],
                "missing_information": missing, "cited_source_refs": list(refs),
                "needs_customer_clarification": parsed["needs_customer_clarification"],
                "answer_evidence": answer_evidence, "reason": parsed["reason"]}

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
                    "source_path": hit.get("source_path"),
                    "source_type": hit.get("source_type"),
                    "kb_architecture": hit.get("kb_architecture"),
                    "navigation_mode": hit.get("navigation_mode"),
                    "coverage_review_mode": hit.get("coverage_review_mode"),
                    "extraction_mode": hit.get("extraction_mode"),
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
            card = cards_by_ref.get(ref, {})
            path = Path(str(card.get("source_path") or ref))
            if not path.exists() or not path.is_file():
                continue
            raw_text = path.read_text(encoding="utf-8")
            body = self._strip_frontmatter(raw_text).strip()
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

    def _fallback_grounded_facts(self, answer_context: list[dict], *, reason: str, user_question: str = "", context_scope: str = "") -> dict[str, Any]:
        facts: list[dict[str, Any]] = []
        for item in answer_context:
            body = str(item.get("text") or "").strip()
            if not body:
                continue
            source_ref = str(item.get("source_ref") or "")
            for index, sentence in enumerate(SENTENCE_SPLIT_RE.split(body.replace("\n", " ")), start=1):
                cleaned = sentence.strip()
                if cleaned and cleaned not in [fact["text"] for fact in facts]:
                    facts.append({"id": f"f{len(facts)+1}", "text": cleaned, "source_refs": [source_ref] if source_ref else [], "conditions": [], "modality": None})
                if len(facts) >= MAX_GROUNDED_FACTS:
                    break
            if len(facts) >= MAX_GROUNDED_FACTS:
                break
        answer_basis = " ".join(fact["text"] for fact in facts[:3]).strip()
        evidence = empty_answer_evidence(user_question or "", context_scope=context_scope, acquisition_status="ready" if facts else "not_found")
        evidence["facts"] = facts
        evidence["coverage"]["status"] = "full" if facts else "none"
        evidence["answer_basis"] = answer_basis
        evidence["source_refs"] = self._extract_source_refs(answer_context)
        evidence["reason"] = reason
        return {
            "grounding_status": "ready" if facts else "not_found",
            "needs_customer_clarification": False,
            "answer_basis": answer_basis,
            "grounded_facts": facts,
            "missing_information": [],
            "cited_source_refs": self._extract_source_refs(answer_context),
            "answer_evidence": evidence,
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
