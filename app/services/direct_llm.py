from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

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

    def respond(
        self,
        text: str,
        kb_result: dict[str, Any] | list[dict],
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
        if response_intent not in {"answer", "clarification", "missing_grounding"}:
            raise ValueError(f"unsupported finalization response_intent: {response_intent}")
        tool_observations = tool_observations or []
        fallback_text = self.prompt_service.render_cannot_answer()
        system_prompt = self._build_finalization_system_prompt(
            self.prompt_service.load_system_prompt(),
            knowledge_mode=knowledge_mode,
        )
        kb_packet = self._coerce_kb_result(kb_result)
        grounding_evidence = self._build_finalization_evidence(kb_packet)
        finalization_conversation = self._build_finalization_conversation(conversation_context)
        tool_facts = self._build_finalization_tool_facts(tool_observations)

        if settings.direct_llm_provider == "stub" and not self._client_injected:
            return self._respond_stub(
                text,
                kb_packet,
                conversation_context=conversation_context,
                tool_observations=tool_observations,
                first_reply_in_dialogue=first_reply_in_dialogue,
                fallback_text=fallback_text,
            )

        user_prompt = json.dumps(
            {
                "task": "Сформулируй клиентский текст только из подтверждённого evidence. Не принимай самостоятельное решение по вопросу клиента и не восполняй детали, которых нет в evidence.",
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
                    "Связанность evidence с темой вопроса не означает, что evidence отвечает на вопрос.",
                    "До формирования текста сначала определи, содержит ли evidence прямой ответ на фактическую часть текущей реплики.",
                    "Если прямого ответа нет, не создавай route=answer и не задавай вопрос, который предполагает неподтверждённый факт.",
                    "В ответе допустима только редактура утверждений, уже содержащихся в evidence. Нельзя выводить новое отношение, процедуру, требование или результат из сочетания нескольких подтверждённых утверждений.",
                    "При response_intent=missing_grounding используй текущую реплику и conversation только для формы естественного ответа; не используй их для выбора, дополнения или вывода фактического содержания.",
                    "Если tool_facts содержит profile_no_answer_option, используй эту profile-declared опцию как следующий шаг и не сообщай клиенту о нехватке evidence.",
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

        normalized = self._normalize_prompt_reply(parsed, fallback_text=fallback_text)
        normalized["response_text"] = self._prepend_standard_greeting_if_missing(
            normalized["response_text"],
            first_reply_in_dialogue=first_reply_in_dialogue,
        )
        normalized["llm_trace"] = list(self._active_llm_trace)
        return normalized

    def next_action(
        self,
        *,
        text: str,
        context: dict,
        tool_observations: list[dict],
        allowed_actions: list[str],
        iteration: int,
    ) -> dict[str, Any]:
        """Ask the customer-facing agent for its next bounded runtime action."""
        self._reset_llm_trace()
        system_prompt = self.prompt_service.load_system_prompt()
        user_prompt = json.dumps(
            {
                "task": "Выбери следующее действие в диалоге. Не отвечай клиенту вне JSON-конверта.",
                "required_json_schema": {
                    "action": "one of allowed_actions",
                    "arguments": "object; for wiki_lookup use {}, for finish use outcome, response_text and optional source_refs",
                    "reason": "short string",
                },
                "allowed_actions": allowed_actions,
                "finish_outcomes": [
                    "social_reply",
                    "out_of_scope",
                    "clarification_requested",
                    "grounded_answer",
                    "cannot_answer",
                ],
                "rules": [
                    "wiki_lookup — единственный источник бизнес-фактов из Wiki.",
                    "Не формируй фактический ответ, пока не получен результат wiki_lookup.",
                    "social_reply, out_of_scope и clarification_requested допустимы без Wiki только без бизнес-фактов.",
                    "Для grounded_answer укажи source_refs только из результата wiki_lookup текущего хода; cannot_answer допустим после lookup даже при пустых source_refs.",
                    "Не используй ключевые слова или скрытые сценарии; выбирай действие по смыслу и истории диалога.",
                ],
                "user_message": text,
                "conversation": self._build_finalization_conversation(context),
                "tool_observations": tool_observations,
                "iteration": iteration,
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
            self._record_llm_call("agent_next_action")
            parsed = json.loads(raw)
        except Exception as exc:
            self._record_llm_call("agent_next_action")
            return {
                "action": "invalid",
                "arguments": {},
                "reason": f"agent_action_error:{type(exc).__name__}",
                "llm_trace": list(self._active_llm_trace),
            }
        if not isinstance(parsed, dict):
            return {"action": "invalid", "arguments": {}, "reason": "agent_action_not_object", "llm_trace": list(self._active_llm_trace)}
        action = str(parsed.get("action") or "").strip()
        if action not in allowed_actions:
            return {"action": "invalid", "arguments": {}, "reason": "agent_action_not_allowed", "llm_trace": list(self._active_llm_trace)}
        arguments = parsed.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        if action == "finish":
            for key in ("outcome", "response_text", "source_refs"):
                if key in parsed and key not in arguments:
                    arguments[key] = parsed[key]
        return {
            "action": action,
            "arguments": arguments,
            "reason": str(parsed.get("reason") or ""),
            "llm_trace": list(self._active_llm_trace),
        }

    @staticmethod
    def _grounded_fallback_context(kb_packet: dict[str, Any]) -> list[dict[str, str]]:
        answer_basis = str(kb_packet.get("answer_basis") or "").strip()
        if answer_basis:
            return [{"text": answer_basis}]
        facts = kb_packet.get("grounded_facts")
        if isinstance(facts, list):
            return [
                {"text": fact.strip()}
                for fact in facts
                if isinstance(fact, str) and fact.strip()
            ]
        return []

    def _render_ready_grounding(self, kb_packet: dict[str, Any]) -> str:
        """Legacy compatibility hook; final customer prose must be generated by the final model."""
        del kb_packet
        return ""

    @staticmethod
    def _as_customer_sentence(text: str) -> str:
        cleaned = text.strip()
        if cleaned and cleaned[-1] not in ".!?…":
            return f"{cleaned}."
        return cleaned

    def respond_social(self, text: str, *, conversation_context: dict | None = None) -> dict:
        """Finish a planner-approved social turn without KB retrieval or cannot_answer UX."""
        self._reset_llm_trace()
        first_reply_in_dialogue = self._is_first_reply_in_context(conversation_context)
        social = self._answer_social_turn(text, conversation_context=conversation_context)
        response_text = self._sanitize_customer_text(str(social.get("response_text") or ""))
        if not response_text or self._contains_forbidden_output(response_text):
            return {
                "route": "answer",
                "response_text": self._prepend_standard_greeting_if_missing(
                    "Пожалуйста!",
                    first_reply_in_dialogue=first_reply_in_dialogue,
                ),
                "confidence": 1.0,
                "reason": str(social.get("reason") or "social_reply_runtime_fallback"),
                "llm_trace": list(self._active_llm_trace),
            }
        return {
            "route": "answer",
            "response_text": self._prepend_standard_greeting_if_missing(
                response_text,
                first_reply_in_dialogue=first_reply_in_dialogue,
            ),
            "confidence": max(float(social.get("confidence") or 0.0), 0.7),
            "reason": str(social.get("reason") or "social_reply"),
            "llm_trace": list(self._active_llm_trace),
        }

    def _normalize_prompt_reply(self, parsed: dict[str, Any], *, fallback_text: str) -> dict:
        route = str(parsed.get("route") or "cannot_answer").strip()
        if route not in {"answer", "cannot_answer", "out_of_scope", "clarification_requested"}:
            route = "cannot_answer"

        response_text = self._sanitize_customer_text(str(parsed.get("response_text") or ""))
        if not response_text:
            route = "cannot_answer"
            response_text = fallback_text

        if self._contains_forbidden_output(response_text):
            route = "cannot_answer"
            response_text = fallback_text

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

    def _contains_forbidden_output(self, text: str) -> bool:
        lowered = text.lower()
        forbidden_tokens = (
            "knowledgebase",
            "datetime",
            "tool",
            "result",
            "input",
            "output",
            "json",
            "kb_snippets",
            "tool_results",
        )
        return any(token in lowered for token in forbidden_tokens) or any(ch in text for ch in "[]{}")

    def _coerce_kb_result(self, kb_result: dict[str, Any] | list[dict]) -> dict[str, Any]:
        if isinstance(kb_result, dict):
            packet = dict(kb_result)
            packet.setdefault("answer_context", packet.get("kb_snippets", []))
            packet.setdefault("grounded_facts", [])
            packet.setdefault("answer_basis", "")
            packet.setdefault("source_refs", self._extract_source_refs(packet.get("answer_context", [])))
            return packet
        answer_context = kb_result if isinstance(kb_result, list) else []
        return {
            "kb_status": "found" if answer_context else "not_found",
            "kb_mode": "legacy_snippets",
            "grounding_status": "ready" if answer_context else "not_found",
            "answer_context": answer_context,
            "grounded_facts": [],
            "answer_basis": "",
            "source_refs": self._extract_source_refs(answer_context),
        }

    @staticmethod
    def _build_finalization_evidence(kb_packet: dict[str, Any]) -> dict[str, Any]:
        """Pass only the compact, client-relevant grounded evidence to the final LLM."""
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

    def _respond_stub(
        self,
        text: str,
        kb_packet: dict[str, Any],
        *,
        conversation_context: dict | None,
        tool_observations: list[dict],
        first_reply_in_dialogue: bool,
        fallback_text: str,
    ) -> dict:
        del text, kb_packet, conversation_context, tool_observations, first_reply_in_dialogue
        return {"route": "cannot_answer", "response_text": fallback_text, "confidence": 0.0, "reason": "stub_cannot_answer"}

    def classify_turn(self, text: str, *, conversation_context: dict | None = None) -> dict:
        profile = self._load_profile_context()

        if settings.direct_llm_provider == "stub" and not self._client_injected:
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
                "conversation_context": conversation_context or {},
                "guidance": [
                    "Use social_turn only for greetings, short acknowledgements, or phatic openers that do not need factual lookup.",
                    "Use knowledge_request for any factual or operational request.",
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
            return self._classify_turn_fallback(text, profile=profile, reason=f"turn_classifier_error:{type(exc).__name__}")

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

    def assess_request(
        self,
        text: str,
        *,
        conversation_context: dict | None = None,
        retrieval: dict | None = None,
        tool_observations: list[dict] | None = None,
        runtime_capabilities: list[dict[str, str]] | None = None,
    ) -> dict:
        self._reset_llm_trace()
        profile = self._load_profile_context()
        active_system_prompt = self.prompt_service.load_system_prompt()
        retrieval = retrieval or {"kb_status": "not_started", "kb_snippets": []}
        tool_observations = tool_observations or []
        runtime_capabilities = runtime_capabilities or []
        kb_status = str(retrieval.get("kb_status") or "not_started")
        kb_hits = retrieval.get("kb_snippets", [])

        if settings.direct_llm_provider == "stub" and not self._client_injected:
            return self._assess_request_stub(
                text,
                profile=profile,
                kb_status=kb_status,
                kb_hits=kb_hits,
                tool_observations=tool_observations,
                conversation_context=conversation_context,
            )

        system_prompt = (
            "You are planning the next bounded-loop action for a customer-facing support agent. "
            "Return JSON only. Do not answer the user directly here. Choose the single best next action."
        )
        user_prompt = json.dumps(
            {
                "task": "Select the next bounded-loop action for this support turn.",
                "required_json_schema": {
                    "action": "read_kb|use_tool|ask_clarification|answer_from_prompt|answer_from_kb|cannot_answer|out_of_scope|social_reply",
                    "scope_status": "in_scope|out_of_scope|uncertain",
                    "confidence": "number 0..1",
                    "reason": "short string",
                    "clarification_question": "optional string",
                },
                "agent_profile": profile,
                "active_system_prompt": active_system_prompt,
                "user_message": text,
                "conversation_context": conversation_context or {},
                "retrieval": retrieval,
                "tool_observations": tool_observations,
                "runtime_capabilities": runtime_capabilities,
                "rules": [
                    "`use_tool` is permitted only when one of runtime_capabilities explicitly supports the requested fact; no other tool, external lookup, contact check, web search, or live-data source exists.",
                    "Before choosing read_kb, compare the customer's question with the active system prompt (`active_system_prompt`). If that prompt explicitly and completely answers the factual question, you MUST choose answer_from_prompt and MUST NOT read KB merely to reconfirm it.",
                    "Use read_kb only when support-domain information may exist in the KB and the active system prompt does not completely answer the question.",
                    "If retrieval.kb_status is found, do not choose read_kb again because the KB has already been gathered for this loop; choose answer_from_kb, use_tool, ask_clarification, or cannot_answer.",
                    "Use use_tool only for an explicitly supported calendar calculation: date, weekday, calendar month/season period, or current year.",
                    "For a calendar month, month range, or season request, preserve the user's stated period in any fallback; never rewrite it as a relative period such as 'after these months'.",
                    "Use answer_from_kb when the gathered KB/tool context is already sufficient for a grounded answer.",
                    "Use the conversation context to resolve short follow-up turns like 'почему', 'как', 'а если', 'то есть', pronouns, or yes/no follow-ups.",
                    "If the previous assistant turn already established the topic, do not ask the user to restate it; prefer read_kb or answer_from_kb.",
                    "Use ask_clarification only if one short question is necessary before any safe answer is possible.",
                    "Use cannot_answer when the request is in scope but grounded information is still insufficient.",
                    "Use out_of_scope when the request is outside the profile domain rather than simply unanswered.",
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
            self._record_llm_call("planner_assess_request")
            parsed: dict[str, Any] = json.loads(raw)
        except Exception as exc:
            self._record_llm_call("planner_assess_request")
            fallback = self._assess_request_stub(
                text,
                profile=profile,
                kb_status=kb_status,
                kb_hits=kb_hits,
                tool_observations=tool_observations,
                conversation_context=conversation_context,
            )
            fallback["reason"] = f"planner_fallback:{type(exc).__name__}"
            fallback["llm_trace"] = list(self._active_llm_trace)
            return fallback

        action = str(parsed.get("action") or "cannot_answer").strip()
        if action not in {
            "read_kb",
            "use_tool",
            "ask_clarification",
            "answer_from_prompt",
            "answer_from_kb",
            "cannot_answer",
            "out_of_scope",
            "social_reply",
        }:
            action = "cannot_answer"

        scope_status = str(parsed.get("scope_status") or "uncertain").strip()
        if scope_status not in {"in_scope", "out_of_scope", "uncertain"}:
            scope_status = "uncertain"

        if action == "read_kb" and kb_status == "found":
            action = "answer_from_kb" if kb_hits else "cannot_answer"

        if action == "use_tool" and tool_observations:
            action = "answer_from_kb" if kb_status == "found" else "read_kb"

        if action == "ask_clarification" and kb_status == "not_started" and self._should_try_kb_before_clarifying(text, conversation_context, profile):
            action = "read_kb"

        return {
            "action": action,
            "scope_status": scope_status,
            "confidence": float(parsed.get("confidence", 0.0)),
            "reason": str(parsed.get("reason") or "loop_planner"),
            "clarification_question": str(parsed.get("clarification_question") or "").strip(),
            "llm_trace": list(self._active_llm_trace),
        }

    def answer(
        self,
        text: str,
        kb_hits: list[dict],
        *,
        allow_general_without_kb: bool = False,
        conversation_context: dict | None = None,
    ) -> dict:
        if not kb_hits and not allow_general_without_kb:
            return {
                "direct_status": "insufficient_confidence",
                "response_text": "",
                "used_kb_sources": [],
                "confidence": 0.0,
                "decision": "handoff",
                "reason": "no_kb_hits",
            }

        if settings.direct_llm_provider == "stub" and not self._client_injected:
            return {
                "direct_status": "insufficient_confidence",
                "response_text": f"Stub direct answer for: {text}",
                "used_kb_sources": [hit["source_ref"] for hit in kb_hits],
                "confidence": 0.4,
                "decision": "handoff",
                "reason": "stub_provider",
            }

        if allow_general_without_kb and not kb_hits:
            return self._answer_social_turn(text, conversation_context=conversation_context)

        kb_context, kb_mode = self._prepare_kb_context(kb_hits)
        answer_context = kb_context
        answer_mode = kb_mode
        engine_trace: dict[str, Any] = {}

        if kb_mode == "llm_wiki_catalog":
            answer_context, answer_mode, engine_trace = self._run_llm_wiki_engine(
                text,
                kb_context,
                conversation_context=conversation_context,
            )

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
                "conversation_context": conversation_context or {},
                "kb_mode": answer_mode,
                "guidance": [
                    "Use only facts grounded in the provided wiki content.",
                    "If kb_mode is llm_wiki_navigation_selected_pages, answer only from the selected full wiki pages.",
                    "If the wiki contains enough information to answer safely, answer directly in Russian and do not hand off.",
                    "Answer briefly and directly; start with the core fact, then add only the minimum necessary detail.",
                    "For short follow-ups like 'почему', answer that exact follow-up from the established topic instead of restating a generic refusal.",
                    "Do not mention KB, snippets, search, materials, internal confidence, or other runtime mechanics.",
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

    def _run_llm_wiki_engine(
        self,
        text: str,
        kb_context: list[dict],
        *,
        conversation_context: dict | None = None,
    ) -> tuple[list[dict], str, dict[str, Any]]:
        navigation = self._plan_llm_wiki_navigation(
            text,
            kb_context,
            conversation_context=conversation_context,
        )
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
                "reason": "coverage_review_skipped_by_config",
            }
        else:
            review = self._review_llm_wiki_coverage(
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

        engine_trace = {
            "navigation": navigation,
            "review": review,
            "selected_source_refs": selected_refs,
        }
        return loaded_pages or kb_context[:5], "llm_wiki_navigation_selected_pages", engine_trace

    def _plan_llm_wiki_navigation(
        self,
        text: str,
        kb_context: list[dict],
        *,
        conversation_context: dict | None = None,
    ) -> dict[str, Any]:
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
                "conversation_context": conversation_context or {},
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
        *,
        conversation_context: dict | None = None,
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
                "conversation_context": conversation_context or {},
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
        _ = (text, kb_hits)
        return response_text

    def _answer_social_turn(self, text: str, *, conversation_context: dict | None = None) -> dict:
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
                "conversation_context": conversation_context or {},
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
            self._record_llm_call("social_reply")
            parsed: dict[str, Any] = json.loads(raw)
        except Exception as exc:
            self._record_llm_call("social_reply")
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


    def _classify_turn_fallback(self, text: str, *, profile: dict, reason: str) -> dict:
        lowered = text.lower().strip()
        social_tokens = {"привет", "здравствуйте", "добрый день", "добрый вечер", "hello", "hi"}
        if lowered in social_tokens:
            return {"turn_type": "social_turn", "confidence": 0.7, "reason": reason}
        if self._looks_out_of_scope(text, profile):
            return {"turn_type": "knowledge_request", "confidence": 0.4, "reason": reason}
        return {"turn_type": "knowledge_request", "confidence": 0.6, "reason": reason}

    def _fallback_answer_from_grounding(
        self,
        text: str,
        answer_context: list[dict],
        *,
        kb_hits: list[dict],
        conversation_context: dict | None,
        reason: str,
        preserve_context_order: bool = False,
    ) -> dict | None:
        """Legacy compatibility hook; application-side business answer rendering is disabled."""
        del text, answer_context, kb_hits, conversation_context, reason, preserve_context_order
        return None

    def _compose_grounded_fallback_answer(
        self,
        text: str,
        answer_context: list[dict],
        *,
        conversation_context: dict | None = None,
    ) -> str:
        """Legacy compatibility hook; final customer prose must be generated by the final model."""
        del text, answer_context, conversation_context
        return ""

    def _collect_grounding_sentences(self, answer_context: list[dict]) -> list[str]:
        sentences: list[str] = []
        for item in answer_context:
            raw_text = self._strip_frontmatter(str(item.get("text", "") or ""))
            compact = re.sub(r"\s+", " ", raw_text).strip()
            if not compact:
                continue
            for part in re.split(r"(?<=[.!?])\s+", compact):
                sentence = part.strip(" -•\t")
                if sentence.startswith("#"):
                    continue
                if len(sentence) < 20:
                    continue
                if sentence not in sentences:
                    sentences.append(sentence)
        return sentences

    def _fallback_query_terms(self, text: str, conversation_context: dict | None) -> list[str]:
        chunks = [text]
        if isinstance(conversation_context, dict):
            recent_messages = conversation_context.get("recent_messages", [])
            if isinstance(recent_messages, list):
                for item in reversed(recent_messages):
                    if not isinstance(item, dict):
                        continue
                    content = str(item.get("content") or "").strip()
                    role = str(item.get("role") or "")
                    if not content:
                        continue
                    if role == "user" and content != text:
                        chunks.append(content)
                        break

        stopwords = {
            "подскажите", "пожалуйста", "можно", "нужно", "нужен", "нужна", "нужны", "хочу", "узнать", "это", "этот", "эта", "что", "как", "почему",
            "какой", "какая", "какие", "про", "для", "без", "если", "или", "ли", "да", "нет", "мне", "нам",
        }
        terms: list[str] = []
        for chunk in chunks:
            for token in re.findall(r"[a-zA-Zа-яА-ЯёЁ]+", chunk.lower()):
                normalized = self._normalize_match_token(token)
                if len(normalized) < 4 or normalized in stopwords:
                    continue
                if normalized not in terms:
                    terms.append(normalized)
        return terms[:10]

    def _normalize_match_text(self, text: str) -> str:
        tokens = [self._normalize_match_token(token) for token in re.findall(r"[a-zA-Zа-яА-ЯёЁ]+", text.lower())]
        return " ".join(token for token in tokens if token)

    def _normalize_match_token(self, token: str) -> str:
        normalized = token.lower().replace("ё", "е")
        for suffix in (
            "иями", "ями", "ами", "ого", "ему", "ому", "ыми", "ими", "иях", "ах", "ях", "ов", "ев", "ие", "ые", "ки", "ок",
            "ий", "ый", "ой", "ая", "яя", "ое", "ее", "ую", "юю", "ам", "ям", "ом", "ем", "а", "я", "ы", "и", "е", "о", "у", "ю",
        ):
            if normalized.endswith(suffix) and len(normalized) - len(suffix) >= 4:
                return normalized[: -len(suffix)]
        return normalized

    def _looks_like_reason_followup(self, text: str) -> bool:
        lowered = text.lower().strip()
        return lowered.startswith(("почему", "а почему", "из-за чего"))

    def _should_try_kb_before_clarifying(self, text: str, conversation_context: dict | None, profile: dict) -> bool:
        if self._looks_out_of_scope(text, profile):
            return False
        if self._is_contextual_followup(text, conversation_context):
            return True
        return bool(self._fallback_query_terms(text, conversation_context))

    def _can_answer_from_found_kb_without_clarification(
        self,
        text: str,
        kb_hits: list[dict],
        conversation_context: dict | None,
    ) -> bool:
        if not kb_hits:
            return False
        kb_context, _ = self._prepare_kb_context(kb_hits)
        return bool(self._compose_grounded_fallback_answer(text, kb_context, conversation_context=conversation_context))

    def _assess_request_stub(
        self,
        text: str,
        *,
        profile: dict,
        kb_status: str,
        kb_hits: list[dict],
        tool_observations: list[dict],
        conversation_context: dict | None = None,
    ) -> dict:
        current_text = text
        if isinstance(conversation_context, dict):
            current_text = str(conversation_context.get("user_message") or text)
        if kb_status == "not_started":
            return {
                "action": "read_kb",
                "scope_status": "uncertain",
                "confidence": 0.6,
                "reason": "stub_read_kb_first",
                "clarification_question": "",
            }

        if kb_status in {"not_found", "not_found_after_search"}:
            if self._looks_out_of_scope(current_text, profile):
                return {
                    "action": "out_of_scope",
                    "scope_status": "out_of_scope",
                    "confidence": 0.8,
                    "reason": "stub_out_of_scope_after_empty_kb",
                    "clarification_question": "",
                }
            return {
                "action": "cannot_answer",
                "scope_status": "in_scope",
                "confidence": 0.6,
                "reason": "stub_in_scope_but_no_grounding",
                "clarification_question": "",
            }

        if self._needs_calendar_tool(current_text, kb_hits) and not tool_observations:
            return {
                "action": "use_tool",
                "scope_status": "in_scope",
                "confidence": 0.85,
                "reason": "stub_calendar_runtime_needed",
                "clarification_question": "",
            }

        if self._looks_out_of_scope(current_text, profile):
            return {
                "action": "out_of_scope",
                "scope_status": "out_of_scope",
                "confidence": 0.75,
                "reason": "stub_out_of_scope_keyword_match",
                "clarification_question": "",
            }

        return {
            "action": "answer_from_kb",
            "scope_status": "in_scope",
            "confidence": 0.8,
            "reason": "stub_answer_from_grounding",
            "clarification_question": "",
        }

    def _is_contextual_followup(self, text: str, conversation_context: dict | None) -> bool:
        lowered = text.lower().strip()
        if not lowered:
            return False
        recent_messages = []
        if isinstance(conversation_context, dict):
            raw_messages = conversation_context.get("recent_messages", [])
            if isinstance(raw_messages, list):
                recent_messages = [item for item in raw_messages if isinstance(item, dict)]
        has_assistant_context = any(str(item.get("role") or "") == "assistant" and str(item.get("content") or "").strip() for item in recent_messages)
        if not has_assistant_context:
            return False
        followup_starts = (
            "почему",
            "а почему",
            "как",
            "а как",
            "то есть",
            "а если",
            "если",
            "можно ли",
            "нельзя ли",
            "да я спросил",
        )
        if lowered.startswith(followup_starts):
            return True
        return len(lowered.split()) <= 4

    def _needs_calendar_tool(self, text: str, kb_hits: list[dict]) -> bool:
        lowered = text.lower()
        kb_text = "\n".join(str(hit.get("text", "")) for hit in kb_hits).lower()
        has_date_hint = (
            any(month in lowered for month in [
                "январ", "феврал", "март", "апрел", "мая", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр"
            ])
            or re.search(r"\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b", lowered) is not None
            or re.search(r"\b\d{4}-\d{2}-\d{2}\b", lowered) is not None
        )
        weekend_rule = any(token in kb_text for token in ("выходн", "суббот", "воскрес"))
        return bool(has_date_hint and weekend_rule)

    def _looks_out_of_scope(self, text: str, profile: dict) -> bool:
        lowered = text.lower()
        domain_terms = self._profile_domain_terms(profile)
        if domain_terms and any(term in lowered for term in domain_terms):
            return False
        obvious_offtopic = ["погода", "политик", "президент", "курс валют", "крипт", "марс", "анекдот", "футбол"]
        return any(term in lowered for term in obvious_offtopic)

    def _profile_domain_terms(self, profile: dict) -> set[str]:
        scope = profile.get("scope", {}) if isinstance(profile, dict) else {}
        in_scope = scope.get("in_scope", []) if isinstance(scope, dict) else []
        terms: set[str] = set()
        if not isinstance(in_scope, list):
            return terms
        for item in in_scope:
            for token in str(item).lower().replace("-", " ").split():
                if len(token) >= 4:
                    terms.add(token)
        return terms

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
