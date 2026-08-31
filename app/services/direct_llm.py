from __future__ import annotations

import json
import re
from typing import Any

from app.core.config import settings
from app.integrations.llm.base import BaseLLMClient
from app.integrations.llm.factory import get_llm_client
from app.integrations.llm.openai_compatible import LLMRecoveryExhausted
from app.services.system_prompt import SystemPromptService

MAX_CATALOG_SELECTION = 3
MAX_REVIEW_ADDITIONS = 2
MAX_SELECTED_WIKI_PAGES = 5


class DirectLLMService:
    def __init__(self, client: BaseLLMClient | None = None, prompt_service: SystemPromptService | None = None) -> None:
        # An injected client is an explicit runtime/test dependency and must not be
        # bypassed merely because the process-wide default provider is `stub`.
        self._client_injected = client is not None
        self.client = client or get_llm_client(
            provider=settings.direct_llm_provider,
            base_url=settings.direct_llm_base_url,
            api_key=settings.direct_llm_api_key,
            api_keys=settings.direct_llm_api_key_list,
            model=settings.direct_llm_model,
            timeout_seconds=settings.direct_llm_timeout_seconds,
            max_retries=settings.direct_llm_max_retries,
            retry_backoff_seconds=settings.direct_llm_retry_backoff_seconds,
            retry_deadline_seconds=settings.direct_llm_retry_deadline_seconds,
        )
        self.temperature = settings.direct_llm_temperature
        self.prompt_service = prompt_service or SystemPromptService()

    def _reset_llm_trace(self) -> None:
        self._active_llm_trace: list[dict[str, Any]] = []

    def _record_llm_call(self, step: str) -> None:
        info = self.client.get_last_call_info() if hasattr(self.client, "get_last_call_info") else {}
        if not info:
            return
        usage = info.get("usage") or {}
        self._active_llm_trace.append(
            {
                "role": "direct_llm",
                "step": step,
                "provider": info.get("provider"),
                "model": info.get("model"),
                "duration_ms": info.get("duration_ms"),
                "attempts": info.get("attempts"),
                "api_key_index": info.get("api_key_index"),
                "used_failover": info.get("used_failover"),
                "failover_count": info.get("failover_count"),
                "failover_events": info.get("failover_events") or [],
                "usage": {
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                },
                "error": info.get("error"),
            }
        )

    def begin_turn(self, *, text: str, context: dict) -> dict[str, Any]:
        """Run one grounded customer turn by requiring the bounded Wiki tool."""
        self._reset_llm_trace()
        conversation = self._build_finalization_conversation(context)
        first_reply = not any(item.get("role") == "assistant" for item in conversation if isinstance(item, dict))
        user_prompt = json.dumps(
            {
                "task": "Обработай текущий ход клиента. По умолчанию сначала вызывай wiki_lookup. Прямой нефактический ответ разрешён только для чистого `Спасибо`, `Ок`, приветствия или иной короткой социальной реплики, в которой нет запроса, вопроса, факта, предложения, проблемы, условия и продолжения ситуации. Любая реплика, где нужен факт об организации, её предложении, стоимости, сроке, порядке, контакте, действии, дате, результате или затруднении, требует wiki_lookup — включая фразу с приветствием. Сам не создавай клиентский ответ с фактами. Не отвечай клиенту вне JSON-конверта.",
                "required_json_schema": {
                    "tool_call": "wiki_lookup or null",
                    "route": "social_reply|cannot_answer|out_of_scope|clarification_requested when tool_call is null",
                    "response_text": "string when tool_call is null; must be absent when tool_call=wiki_lookup",
                    "confidence": "number 0..1 when tool_call is null",
                    "reason": "short string",
                },
                "user_message": text,
                "first_reply_in_dialogue": first_reply,
                "conversation": conversation,
                "rules": [
                    "wiki_lookup — единственный источник бизнес-фактов из Wiki.",
                    "По умолчанию вызывай wiki_lookup; откажись от него только для чистой короткой социальной реплики без предметного содержания.",
                    "Решение до инструментов: если реплика не является чистой короткой социальной, единственный допустимый JSON — {\"tool_call\":\"wiki_lookup\",\"reason\":\"need_confirmed_facts\"}. Не возвращай route, response_text или confidence на этом шаге.",
                    "Обязательно вызови wiki_lookup для любого вопроса или ситуации, где нужен факт об организации, её предложении, стоимости, сроке, порядке, контакте, действии, дате, условии, проблеме, невыполненном ожидаемом результате, ожидании результата или затруднении — даже если сообщение начинается с приветствия и даже без вопросительного знака.",
                    "До wiki_lookup нельзя возвращать клиентский текст, содержащий бизнес-факт, процедуру, обещание, контакт, объяснение результата, уточнение по предметной области, cannot_answer или out_of_scope.",
                    "social_reply разрешён только для очевидной чисто социальной реплики, которая не содержит и не продолжает запрос, проблему или ситуацию клиента; в нём не должно быть бизнес-фактов, процедуры, обещаний или следующего шага.",
                    "Не используй ключевые слова, скрытые сценарии, историю диалога, память модели или приложение как источник бизнес-ответа; выбирай действие по смыслу диалога и вызывай Wiki при малейшей потребности в фактах.",
                    "Верни только JSON-объект по указанной схеме.",
                ],
            },
            ensure_ascii=False,
        )
        try:
            raw = self.client.generate(
                system_prompt=self.prompt_service.load_system_prompt(),
                user_prompt=user_prompt,
                temperature=self.temperature,
                response_format={"type": "json_object"},
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "wiki_lookup",
                            "description": "Read the Wiki before any factual or situation-specific customer answer.",
                            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                        },
                    }
                ],
                tool_choice="auto",
            )
            self._record_llm_call("customer_turn")
            parsed = json.loads(raw)
        except Exception as exc:
            self._record_llm_call("customer_turn")
            return {
                "kind": "final",
                "result": {
                    "route": "retry_pending",
                    "response_text": "",
                    "confidence": 0.0,
                    "reason": "llm_recovery_exhausted" if isinstance(exc, LLMRecoveryExhausted) else f"customer_turn_error:{type(exc).__name__}",
                },
                "llm_trace": list(self._active_llm_trace),
            }
        if not isinstance(parsed, dict):
            return self._invalid_begin_turn("customer_turn_not_object")
        native_calls = parsed.get("_native_tool_calls")
        if isinstance(native_calls, list) and any(
            isinstance(call, dict) and isinstance(call.get("function"), dict) and call["function"].get("name") == "wiki_lookup"
            for call in native_calls
        ):
            return {"kind": "wiki_lookup", "llm_trace": list(self._active_llm_trace)}
        if parsed.get("tool_call") == "wiki_lookup":
            return {"kind": "wiki_lookup", "llm_trace": list(self._active_llm_trace)}
        normalized = self._normalize_prompt_reply(parsed)
        if normalized["route"] not in {"social_reply", "cannot_answer", "out_of_scope", "clarification_requested"}:
            return self._invalid_begin_turn("customer_turn_requires_wiki")
        return {"kind": "final", "result": normalized, "llm_trace": list(self._active_llm_trace)}

    def _invalid_begin_turn(self, reason: str) -> dict[str, Any]:
        return {
            "kind": "final",
            "result": {"route": "retry_pending", "response_text": "", "confidence": 0.0, "reason": reason},
            "llm_trace": list(self._active_llm_trace),
        }

    def respond(
        self,
        text: str,
        kb_result: dict[str, Any],
        *,
        knowledge_mode: str = "kb_grounded",
        conversation_context: dict | None = None,
        tool_observations: list[dict] | None = None,
        first_reply_in_dialogue: bool = False,
        response_intent: str = "answer",
    ) -> dict:
        self._reset_llm_trace()
        if knowledge_mode not in {"prompt_only", "kb_grounded"}:
            raise ValueError(f"unsupported finalization knowledge_mode: {knowledge_mode}")
        if response_intent not in {"answer", "clarification", "missing_grounding", "social_reply"}:
            raise ValueError(f"unsupported finalization response_intent: {response_intent}")
        tool_observations = tool_observations or []
        system_prompt = self._build_finalization_system_prompt(
            self.prompt_service.load_system_prompt(),
            knowledge_mode=knowledge_mode,
        )
        kb_packet = self._coerce_kb_result(kb_result)
        grounding_evidence = self._build_finalization_evidence(kb_packet)
        finalization_conversation = self._build_finalization_conversation(conversation_context)
        tool_facts = self._build_finalization_tool_facts(tool_observations)

        user_prompt = json.dumps(
            {
                "task": "При response_intent=answer и knowledge_mode=kb_grounded подготовь готовый прямой ответ на текущий вопрос только из evidence. Не заменяй такой ответ уточняющим вопросом, cannot_answer или рассуждением о дальнейшей проверке. Если текущая реплика прямо отвечает на предыдущий вопрос ассистента, прими её как состояние диалога и продолжи ответ; не повторяй тот же вопрос.",
                "knowledge_mode": knowledge_mode,
                "response_intent": response_intent,
                "required_json_schema": {
                    "route": "answer|social_reply|cannot_answer|out_of_scope|clarification_requested",
                    "response_text": "string",
                    "confidence": "number 0..1",
                    "reason": "short string",
                },
                "user_message": text,
                "first_reply_in_dialogue": first_reply_in_dialogue,
                "conversation": finalization_conversation,
                "grounding_evidence": grounding_evidence,
                "tool_facts": tool_facts,
                "output_rules": [
                    "Верни только JSON-объект по указанной схеме.",
                    "Приоритеты finalizer: подтверждённая фактическая точность выше полноты, полезности и стилистической гладкости ответа.",
                    "Ты редактор подтверждённого evidence, а не самостоятельный решатель вопроса клиента.",
                    "Формируй ответ только как естественную редактуру grounding_evidence и tool_facts; не закрывай непокрытую evidence часть вопроса рассуждением, догадкой или общими знаниями.",
                    "Сообщение клиента и conversation служат только для понимания контекста диалога и не подтверждают новые факты.",
                    "Каждое фактическое утверждение в response_text должно прямо следовать из grounding_evidence или tool_facts.",
                    "Системный промпт задаёт роль и правила общения, но не является источником сведений о предметной области.",
                    "Если grounding_evidence пуст, используй текущую реплику и весь доступный conversation только для понимания контекста диалога; не превращай их в источник фактических утверждений, не подменяй ответ шаблонной заглушкой и не делай вид, что контекст диалога неизвестен.",
                    "При knowledge_mode=kb_grounded считай grounding_evidence подтверждённым на предыдущем этапе и не выходи за его фактические границы.",
                    "answer_basis — служебное краткое описание evidence, а не самостоятельный источник фактов и не требование закрыть вопрос клиента.",
                    "Факты в grounding_evidence — это доказательства, а не порядок построения фразы; не пересказывай цепочку вывода вместо результата.",
                    "Сформулируй готовый естественный ответ на русском языке.",
                    "Не склеивай извлечённые факты механически.",
                    "Сохраняй смысловые связи между субъектом, действием, условием и способом действия.",
                    "Перед отправкой проверь, что фраза грамматически закончена и не меняет подтверждённый смысл evidence.",
                    "Перед возвратом JSON внутренне сверь каждое фактическое утверждение черновика с grounding_evidence и tool_facts; убери утверждение, которое не имеет прямого подтверждения.",
                    "Не превращай правдоподобное предположение, общий опыт модели или формулировку клиента в фактическое утверждение.",
                    "Сохраняй точную модальность подтверждённых фактов: «обычно», «может», «зависит», «рекомендуется» нельзя усиливать до «только», «всегда», «точно», «обязательно» или другого более сильного утверждения.",
                    "Общее вероятностное правило не доказывает исход конкретного случая: если evidence говорит «обычно» или оставляет условия/исключения, не отвечай категорическим «да» или «нет» о конкретной дате; сообщи об общем правиле и безопасном способе уточнить конкретный случай.",
                    "После прямого ответа не добавляй другие подтверждённые факты, если клиент прямо не спрашивал о них и они не нужны, чтобы понять этот ответ или выполнить требуемое действие.",
                    "Не добавляй новые факты и не показывай внутренний процесс, инструменты, источники или причины выбора ответа.",
                    "Если клиент прямо спрашивает «почему», объясни результат только подтверждёнными фактами.",
                    "Если evidence недостаточно для прямого ответа и response_intent=clarification, задай один естественный, конкретный и полезный уточняющий вопрос по текущей реплике; не говори, что клиент задал вопрос, если вопроса не было.",
                    "При response_intent=social_reply верни route=social_reply и короткий естественный ответ без бизнес-фактов, KB и следующего шага из политики.",
                    "Связанность evidence с темой вопроса не означает, что evidence отвечает на вопрос.",
                    "До формирования текста сначала определи, содержит ли evidence прямой ответ на фактическую часть текущей реплики.",
                    "Если прямого ответа нет, не создавай route=answer и не задавай вопрос, который предполагает неподтверждённый факт.",
                    "В ответе допустима только редактура утверждений, уже содержащихся в evidence. Нельзя выводить новое отношение, процедуру, требование или результат из сочетания нескольких подтверждённых утверждений.",
                    "При response_intent=missing_grounding используй текущую реплику и conversation только для формы естественного ответа; не используй их для выбора, дополнения или вывода фактического содержания.",
                    "При response_intent=missing_grounding, если tool_facts содержит profile_no_answer_option, составь естественный cannot_answer и включи summary этой опции как следующий шаг.",
                    "Если evidence не содержит прямого ответа на фактическую часть текущей реплики, не возвращай route=answer.",
                    "Верни route=clarification_requested только когда один естественный уточняющий вопрос может привести к подтверждённому ответу; иначе верни route=cannot_answer с естественным индивидуальным текстом без фактических утверждений и без шаблонной фразы.",
                ],
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
            self._record_llm_call("final_response")
            parsed: dict[str, Any] = json.loads(raw)
        except Exception as exc:
            self._record_llm_call("final_response")
            self._active_llm_trace.append(
                {
                    "role": "direct_llm",
                    "step": "final_response_failed_closed",
                    "error": type(exc).__name__,
                    "customer_reply_emitted": False,
                }
            )
            return {
                "route": "retry_pending",
                "response_text": "",
                "confidence": 0.0,
                "reason": "llm_recovery_exhausted" if isinstance(exc, LLMRecoveryExhausted) else f"final_response_error:{type(exc).__name__}",
                "llm_trace": list(self._active_llm_trace),
            }

        normalized = self._normalize_prompt_reply(parsed)
        if response_intent == "social_reply" and normalized["route"] != "retry_pending":
            normalized["route"] = "social_reply"
        normalized["llm_trace"] = list(self._active_llm_trace)
        return normalized

    def _normalize_prompt_reply(self, parsed: dict[str, Any]) -> dict:
        route = str(parsed.get("route") or "cannot_answer").strip()
        if route not in {"answer", "social_reply", "cannot_answer", "out_of_scope", "clarification_requested"}:
            route = "cannot_answer"

        response_text = self._sanitize_customer_text(str(parsed.get("response_text") or ""))
        if not response_text or self._contains_internal_envelope(response_text):
            return {
                "route": "retry_pending",
                "response_text": "",
                "confidence": 0.0,
                "reason": "invalid_finalizer_output",
            }

        return {
            "route": route,
            "response_text": response_text,
            "confidence": float(parsed.get("confidence", 0.0)),
            "reason": str(parsed.get("reason") or "prompt_runtime"),
        }

    def _sanitize_customer_text(self, text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text).strip()
        cleaned = cleaned.replace("**", "").replace("__", "")
        return cleaned[:1000]

    @staticmethod
    def _prepend_standard_greeting_if_missing(text: str, *, first_reply_in_dialogue: bool) -> str:
        if not first_reply_in_dialogue:
            return text
        greeting_pattern = re.compile(
            r"^\s*(?:здравствуй(?:те)?|добрый\s+(?:день|вечер)|доброе\s+утро|привет(?:ствую)?|"
            r"доброго\s+времени\s+суток|рад(?:а)?\s+(?:вас\s+)?приветствовать)\b",
            flags=re.IGNORECASE,
        )
        if greeting_pattern.search(text):
            return text
        return f"Здравствуйте! {text}"

    @staticmethod
    def _is_first_reply_in_context(conversation_context: dict | None) -> bool:
        if not conversation_context:
            return False
        recent_messages = conversation_context.get("recent_messages", [])
        return isinstance(recent_messages, list) and not any(
            isinstance(item, dict) and str(item.get("role") or "") == "assistant"
            for item in recent_messages
        )

    def _contains_internal_envelope(self, text: str) -> bool:
        lowered = text.casefold()
        return "knowledgebase result:" in lowered or "kb_snippets" in lowered or "tool_results" in lowered

    def _coerce_kb_result(self, kb_result: dict[str, Any]) -> dict[str, Any]:
        packet = dict(kb_result)
        packet.setdefault("answer_context", packet.get("kb_snippets", []))
        packet.setdefault("grounded_facts", [])
        packet.setdefault("answer_basis", "")
        source_refs = packet.get("source_refs", [])
        packet["source_refs"] = [str(ref) for ref in source_refs if str(ref).strip()] if isinstance(source_refs, list) else []
        return packet

    @staticmethod
    def _build_finalization_evidence(kb_packet: dict[str, Any]) -> dict[str, Any]:
        """Pass only the compact, client-relevant grounded evidence to the final LLM."""
        if str(kb_packet.get("grounding_status") or "") != "ready":
            return {"answer_basis": "", "facts": []}
        return {
            "answer_basis": str(kb_packet.get("answer_basis") or "").strip(),
            "facts": [
                fact.strip()
                for fact in kb_packet.get("grounded_facts", [])
                if isinstance(fact, str) and fact.strip()
            ],
        }

    @staticmethod
    def _build_finalization_conversation(conversation_context: dict | None) -> list[dict[str, str]]:
        """Project runtime context to role-labelled customer dialogue only."""
        if not isinstance(conversation_context, dict):
            return []
        recent_messages = conversation_context.get("recent_messages", [])
        if not isinstance(recent_messages, list):
            return []
        projected: list[dict[str, str]] = []
        for item in recent_messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                projected.append({"role": role, "content": content})
        return projected[-10:]

    @staticmethod
    def _build_finalization_tool_facts(tool_observations: list[dict]) -> list[dict[str, str]]:
        """Project tool output to normalized customer-relevant facts only."""
        projected: list[dict[str, str]] = []
        for item in tool_observations:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "").strip()
            summary = str(item.get("summary") or "").strip()
            if kind and summary:
                projected.append({"kind": kind, "summary": summary})
        return projected

    @staticmethod
    def _build_finalization_system_prompt(active_system_prompt: str, *, knowledge_mode: str) -> str:
        """Add the application-owned evidence boundary at system-message priority."""
        contract = f"""

## Runtime finalization contract

The application has selected knowledge_mode={knowledge_mode}.
This section governs how the final customer answer is composed; the profile prompt above defines role and communication style only.

- In prompt_only mode, do not make factual claims about the subject domain. Produce only a social response or a necessary clarification.
- In kb_grounded mode, grounding_evidence has already passed the knowledge boundary and is the only source of subject-domain facts for this turn.
- When answer_basis directly answers the current customer question, preserve that answer as the factual core. Rephrase it naturally and keep it within the exact scope and modality of facts.
- State the direct practical conclusion first. Use the supplied dialogue to resolve short follow-ups and determine which supported option, requirement, or next action applies to this customer; do not replace that conclusion with a bare list of eligibility facts. Then add only the relevant confirmed conditions.
- When the current customer message states or narrows a constraint or preference, explicitly acknowledge that constraint or preference in the first sentence before applying the grounded facts. Do not answer as though the message were a new standalone request.
- Keep the most recent explicit customer constraint or preference active across later short follow-ups. Do not switch back to an earlier alternative unless the customer changes the constraint or asks for a comparison.
- When the customer asks whether other requirements exist, do not claim that none exist if grounding_evidence contains relevant conditions. List those confirmed conditions; if the evidence is not exhaustive, avoid an exhaustive "no other requirements" claim.
- Do not invent a new distinction, missing prerequisite, prohibition, or uncertainty that is not present in the supplied evidence.
- Select only evidence relevant to the current question; never mechanically concatenate every fact and never expose internal mechanics.
""".strip()
        return f"{active_system_prompt.rstrip()}\n\n{contract}"
