from __future__ import annotations

import json
import re
from typing import Any

from app.core.config import settings
from app.integrations.llm.base import BaseLLMClient
from app.integrations.llm.factory import get_llm_client
from app.integrations.llm.openai_compatible import LLMRecoveryExhausted
from app.services.audit import capture_model_input
from app.services.final_response_validation import (
    clean_customer_text,
    validate_final_response,
)
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
            drop_params=settings.openai_compatible_drop_params,
        )
        self.temperature = settings.direct_llm_temperature
        self.prompt_service = prompt_service or SystemPromptService()

    def _reset_llm_trace(self) -> None:
        self._active_llm_trace: list[dict[str, Any]] = []

    def _record_llm_call(self, step: str, input_packet: dict) -> None:
        info = self.client.get_last_call_info() if hasattr(self.client, "get_last_call_info") else {}
        info = info or {}
        usage = info.get("usage") or {}
        self._active_llm_trace.append(
            {
                "entry_kind": "model_call",
                "role": "direct_llm",
                "step": step,
                "input_packet": input_packet,
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
        """Run one grounded customer turn with typed native tool requests."""
        self._reset_llm_trace()
        user_prompt = self._build_selector_prompt(text=text, context=context)
        recorded_call = False
        try:
            raw = self.client.generate(
                system_prompt=self.prompt_service.load_system_prompt(),
                user_prompt=user_prompt,
                temperature=self.temperature,
                tools=self._native_tools(),
                tool_choice="auto",
                parallel_tool_calls=False,
            )
            self._record_llm_call("customer_turn", capture_model_input("begin_turn", json.loads(user_prompt)))
            recorded_call = True
            parsed = self._parse_json_object(raw)
        except Exception as exc:
            if not recorded_call:
                self._record_llm_call("customer_turn", capture_model_input("begin_turn", json.loads(user_prompt)))
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
        return self._selector_decision(parsed)

    def _build_selector_prompt(self, *, text: str, context: dict) -> str:
        conversation = self._build_finalization_conversation(context)
        first_reply = not any(item.get("role") == "assistant" for item in conversation if isinstance(item, dict))
        tool_observations = self._project_tool_observations(context)
        tool_state = {"wiki_lookup": "not_started", "calendar_lookup": "not_started"}
        for observation in tool_observations:
            if observation["kind"] in tool_state:
                tool_state[observation["kind"]] = observation.get("status", "unavailable")
        remaining_tool_calls = sum(status == "not_started" for status in tool_state.values())
        return json.dumps(
            {
                "task": "Выбери следующее действие для текущего хода клиента. Если для ответа ещё нужны сведения зарегистрированного инструмента, который не вызывался в этом ходу, вызови один native-инструмент: calendar_lookup или wiki_lookup. Иначе заверши сбор сведений JSON-объектом с action=finalize по указанной схеме. После результата одного инструмента можно вызвать другой, если его сведения нужны для ответа; готовность одного результата не означает достаточность всех сведений. Чистая социальная реплика может перейти к финализации без инструментов. На этом этапе не создавай клиентский текст или его черновик: его сформирует одна общая финализация после сборки пакета.",
                "required_json_schema": {
                    "action": "finalize",
                    "response_intent": "answer|social_reply|clarification|missing_grounding",
                    "reason": "Короткое основание выбора действия без рассуждений и клиентского текста.",
                },
                "user_message": text,
                "first_reply_in_dialogue": first_reply,
                "conversation": conversation,
                "tool_observations": tool_observations,
                "tool_state": tool_state,
                "remaining_tool_calls": remaining_tool_calls,
                "rules": [
                    "calendar_lookup — не источник бизнес-фактов, а факт о календаре из текущего текста; используйте его только если вопрос или уточнение зависит от даты, дня недели или календарного периода.",
                    "wiki_lookup — единственный источник бизнес-фактов из Wiki.",
                    "Для любого содержательного ответа можно использовать не более двух последовательных native-инструментов без параллельных вызовов.",
                    "Каждый зарегистрированный инструмент можно вызвать не более одного раза за ход. Повторный, третий, неизвестный или параллельный вызов нарушает контракт. После двух вызовов допустимо только завершение сбора через action=finalize, без клиентского текста.",
                    "Если для ответа не хватает календарного факта, можно запросить calendar_lookup, а затем при необходимости wiki_lookup; если не хватает бизнес-факта, можно запросить wiki_lookup, а затем при необходимости calendar_lookup.",
                    "Каждый tool_call должен быть exact-name match и передавать JSON-объект с точной схемой аргументов. Не используй строки вместо JSON, не добавляй лишних ключей и не запрашивай параллельные инструменты.",
                    "Любой неизвестный инструмент, отсутствующие аргументы, лишние аргументы, не-JSON arguments или параллельные native_tool_calls считаются ошибкой и должны приводить к retry_pending без клиентского текста.",
                    "Не возвращай route, response_text, confidence или клиентский черновик на этапе выбора действий. Для завершения верни только action, response_intent и reason. Значение social_reply допустимо только для чистой социальной реплики без содержательного запроса; оно не разрешает обходить получение нужных фактов.",
                    "В arguments передавай только смысловую цель поиска и контекстное ограничение; не вписывай туда предполагаемые бизнес-факты, контакты, ответы или инструкции.",
                    "Для calendar_lookup используй exact-name JSON с ключами date_expression и requested_calendar_fact; date_expression должен отражать фрагмент текущей реплики, а requested_calendar_fact — только то календарное наблюдение, которое нужно подтвердить.",
                    "Для wiki_lookup используй exact-name JSON с ключами query, context_scope и needed_fact; query должен быть семантическим, а не словарным.",
                    "Не используй ключевые слова, скрытые сценарии, историю диалога, память модели или приложение как источник бизнес-ответа; выбирай действие по смыслу диалога и вызывай нужный инструмент при малейшей потребности в фактах.",
                    "Для wiki_lookup context_scope обязан сохранять последний явно выбранный клиентом предметный вариант из conversation. Нельзя заменять такой вариант более общим родовым словом; если клиент не просит сравнение или смену варианта, query и needed_fact должны быть сформулированы только для выбранного варианта.",
                ],
            },
            ensure_ascii=False,
        )

    def _selector_decision(self, parsed: object) -> dict[str, Any]:
        if not isinstance(parsed, dict):
            return self._invalid_begin_turn("invalid_selector_output")
        if "_native_tool_calls" in parsed:
            calls = parsed["_native_tool_calls"]
            if not isinstance(calls, list) or len(calls) != 1 or any(k in parsed for k in {"action", "route", "response_text", "confidence"}):
                return self._invalid_begin_turn("native_tool_request_invalid")
            call = self._native_registered_tool_call(calls, registered_tools={"wiki_lookup", "calendar_lookup"})
            ident = calls[0].get("id") if isinstance(calls[0], dict) else None
            if call is None or call["name"] not in {"wiki_lookup", "calendar_lookup"} or not isinstance(ident, str) or not ident.strip():
                return self._invalid_begin_turn("native_tool_request_invalid")
            self._active_llm_trace[-1]["native_tool_call_id"] = ident
            return {"kind": call["name"], "tool_request": call["arguments"], "tool_call_id": ident, "llm_trace": list(self._active_llm_trace)}
        if set(parsed) != {"action", "response_intent", "reason"} or parsed.get("action") != "finalize" or not isinstance(parsed.get("response_intent"), str) or parsed["response_intent"] not in {"answer", "social_reply", "clarification", "missing_grounding"} or not isinstance(parsed.get("reason"), str):
            return self._invalid_begin_turn("invalid_selector_output")
        return {"kind": "finalization_requested", "response_intent": parsed["response_intent"], "reason": parsed["reason"], "llm_trace": list(self._active_llm_trace)}

    @staticmethod
    def _parse_json_object(raw: str) -> Any:
        """Parse the first JSON object when a compatible provider appends junk."""
        candidate = str(raw or "").lstrip()
        value, _ = json.JSONDecoder().raw_decode(candidate)
        return value

    def continue_after_tool(
        self,
        *,
        text: str,
        context: dict,
        tool_name: str,
        tool_call_id: str,
        tool_request: dict[str, str],
        observation: dict[str, Any],
    ) -> dict[str, Any]:
        """Continue an OpenAI native tool conversation with a linked tool result."""
        self._reset_llm_trace()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.prompt_service.load_system_prompt()},
            {"role": "user", "content": json.dumps({**json.loads(self._build_selector_prompt(text=text, context=context)), "tool_requests": context.get("tool_requests", []), "tool_observations": context.get("tool_observations", [])}, ensure_ascii=False)},
            {"role": "assistant", "tool_calls": [{"id": tool_call_id, "type": "function", "function": {"name": tool_name, "arguments": json.dumps(tool_request, ensure_ascii=False)}}]},
            {"role": "tool", "tool_call_id": tool_call_id, "content": json.dumps(observation, ensure_ascii=False)},
            {"role": "user", "content": json.dumps({"task": "Продолжи выбор действий по текущему вопросу и полученным результатам инструментов. Если для ответа ещё нужны сведения другого зарегистрированного инструмента, который не вызывался в этом ходу, вызови его через native tool call. Всего допустимо не более двух последовательных вызовов, каждый инструмент — не более одного раза. Иначе верни только JSON с action=finalize, response_intent=answer|social_reply|clarification|missing_grounding и коротким reason. Не создавай клиентский ответ, route, response_text, confidence или черновик. Все полученные результаты будут переданы одной общей финализации.", "required_json_schema": {"action": "finalize", "response_intent": "answer|social_reply|clarification|missing_grounding", "reason": "Короткое основание выбора действия без рассуждений и клиентского текста."}}, ensure_ascii=False)},
        ]
        history = context.get("native_tool_messages")
        if history is not None:
            if not isinstance(history, list) or not history or len(history) > 4 or len(history) % 2:
                return self._invalid_begin_turn("tool_call_id_mismatch")
            seen = set()
            seen_tools = set()
            try:
                for index in range(0, len(history), 2):
                    assistant, tool = history[index:index + 2]
                    calls = assistant["tool_calls"]
                    if assistant["role"] != "assistant" or tool["role"] != "tool" or len(calls) != 1:
                        return self._invalid_begin_turn("tool_call_id_mismatch")
                    validated = self._native_registered_tool_call(calls, registered_tools={"wiki_lookup", "calendar_lookup"})
                    if validated is None or validated["name"] in seen_tools:
                        return self._invalid_begin_turn("tool_call_id_mismatch")
                    seen_tools.add(validated["name"])
                    ident = calls[0]["id"]
                    if not isinstance(ident, str) or not ident.strip() or ident in seen or tool["tool_call_id"] != ident:
                        return self._invalid_begin_turn("tool_call_id_mismatch")
                    seen.add(ident)
                latest = history[-2]["tool_calls"][0]
                if latest["id"] != tool_call_id or latest["function"]["name"] != tool_name or json.loads(latest["function"]["arguments"]) != tool_request or json.loads(history[-1]["content"]) != observation:
                    return self._invalid_begin_turn("tool_call_id_mismatch")
            except (KeyError, TypeError, ValueError, IndexError):
                return self._invalid_begin_turn("tool_call_id_mismatch")
            messages[2:4] = history
        if not isinstance(tool_call_id, str) or not tool_call_id.strip() or self._validated_legacy_tool_request(tool_name, tool_request) is None:
            return self._invalid_begin_turn("tool_request_invalid")
        used_tools = {tool_name}
        if history:
            used_tools.update(message["tool_calls"][0]["function"]["name"] for message in history if message.get("role") == "assistant")
        available_tools = [tool for tool in self._native_tools() if tool["function"]["name"] not in used_tools]
        # Capability advertisement follows the same hard budget as execution.
        # This does not choose a tool or synthesize a finalize decision.
        selection_options: dict[str, Any] = {"tools": available_tools, "tool_choice": "auto", "parallel_tool_calls": False} if available_tools else {"response_format": {"type": "json_object"}}
        recorded_call = False
        try:
            raw = self.client.generate(
                system_prompt=self.prompt_service.load_system_prompt(), user_prompt="", temperature=self.temperature,
                messages=messages, **selection_options,
            )
            self._record_llm_call("tool_result_selection", capture_model_input("continue_after_tool", {**json.loads(messages[1]["content"]), "tool_observation": observation}))
            recorded_call = True
            parsed = self._parse_json_object(raw)
        except Exception as exc:
            if not recorded_call:
                self._record_llm_call("tool_result_selection", capture_model_input("continue_after_tool", {**json.loads(messages[1]["content"]), "tool_observation": observation}))
            self._active_llm_trace.append({"role": "direct_llm", "step": "tool_result_finalization_failed", "error": type(exc).__name__})
            return self._invalid_begin_turn("tool_result_finalization_failed")
        return self._selector_decision(parsed)

    def _project_tool_observations(self, context: dict) -> list[dict[str, Any]]:
        observations = context.get("tool_observations") if isinstance(context, dict) else []
        projected: list[dict[str, Any]] = []
        if not isinstance(observations, list):
            return projected
        for item in observations:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or item.get("tool") or "").strip()
            if kind:
                projected.append({
                    "kind": kind,
                    **{k: v for k, v in item.items() if k in {"summary", "structured", "status", "source_refs"}},
                })
        return projected[-2:]

    @staticmethod
    def _native_tools() -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "wiki_lookup",
                    "description": "Read the Wiki before any factual or situation-specific customer answer.",
                    "parameters": {
                        "type": "object",
                        "required": ["query", "context_scope", "needed_fact"],
                        "properties": {
                            "query": {"type": "string"},
                            "context_scope": {"type": "string"},
                            "needed_fact": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "calendar_lookup",
                    "description": "Validate a calendar fact from the current customer message.",
                    "parameters": {
                        "type": "object",
                        "required": ["date_expression", "requested_calendar_fact"],
                        "properties": {
                            "date_expression": {"type": "string"},
                            "requested_calendar_fact": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
            },
        ]

    @staticmethod
    def _native_registered_tool_call(calls: list[object], *, registered_tools: set[str]) -> dict[str, Any] | None:
        """Bind a native call to a registered tool and validate its arguments."""
        valid_calls: list[dict[str, Any]] = []
        for call in calls:
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            if not isinstance(name, str) or name not in registered_tools:
                continue
            raw_arguments = function.get("arguments")
            try:
                arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
            except json.JSONDecodeError:
                continue
            if name == "wiki_lookup":
                request = DirectLLMService._validated_wiki_request(arguments)
            elif name == "calendar_lookup":
                request = DirectLLMService._validated_calendar_request(arguments)
            else:
                request = None
            if request is not None:
                valid_calls.append({"name": name, "arguments": request, "id": str(call.get("id") or "")})
        if len(valid_calls) == 1:
            return valid_calls[0]
        if len(valid_calls) > 1:
            return {"name": "__parallel__", "arguments": {}, "id": ""}
        return None

    @staticmethod
    def _validated_legacy_tool_request(tool_name: str, arguments: object) -> dict[str, str] | None:
        if tool_name == "wiki_lookup":
            return DirectLLMService._validated_wiki_request(arguments)
        if tool_name == "calendar_lookup":
            return DirectLLMService._validated_calendar_request(arguments)
        return None

    @staticmethod
    def _validated_wiki_request(arguments: object) -> dict[str, str] | None:
        if not isinstance(arguments, dict) or set(arguments) != {"query", "context_scope", "needed_fact"} or not all(isinstance(value, str) for value in arguments.values()):
            return None
        request = {key: str(arguments.get(key) or "").strip() for key in ("query", "context_scope", "needed_fact")}
        return request if all(request.values()) else None

    @staticmethod
    def _validated_calendar_request(arguments: object) -> dict[str, str] | None:
        if not isinstance(arguments, dict) or set(arguments) != {"date_expression", "requested_calendar_fact"} or not all(isinstance(value, str) for value in arguments.values()):
            return None
        request = {key: str(arguments.get(key) or "").strip() for key in ("date_expression", "requested_calendar_fact")}
        return request if all(request.values()) else None

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
                "task": "При response_intent=answer подготовь готовый прямой ответ на текущий вопрос только из evidence. При knowledge_mode=kb_grounded подтверждёнными evidence являются grounding_evidence и tool_facts; ready-результат календарного инструмента является прямым календарным evidence и должен использоваться для ответа на зависящий от даты вопрос. Не заменяй такой ответ уточняющим вопросом, cannot_answer или рассуждением о дальнейшей проверке. Если текущая реплика прямо отвечает на предыдущий вопрос ассистента, прими её как состояние диалога и продолжи ответ; не повторяй тот же вопрос.",
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
                    "Если tool_facts содержит готовый календарный результат, используй его точные дату, день недели и признак выходного как единственные допустимые календарные значения; не пересчитывай, не заменяй и не дополняй их.",
                    "Системный промпт задаёт роль и правила общения, но не является источником сведений о предметной области.",
                    "Если grounding_evidence пуст, используй текущую реплику и весь доступный conversation только для понимания контекста диалога; не превращай их в источник фактических утверждений, не подменяй ответ шаблонной заглушкой и не делай вид, что контекст диалога неизвестен.",
                    "При knowledge_mode=kb_grounded считай grounding_evidence подтверждённым на предыдущем этапе и не выходи за его фактические границы.",
                    "answer_basis — служебное краткое описание evidence, а не самостоятельный источник фактов и не требование закрыть вопрос клиента.",
                    "Факты в grounding_evidence — это доказательства, а не порядок построения фразы; не пересказывай цепочку вывода вместо результата.",
                    "Сформулируй готовый естественный ответ на русском языке.",
                    "Сохраняй выраженное ранее клиентом предметное ограничение или выбранный вариант: если текущая реплика ссылается на тот же предмет неявно или шире, продолжай именно этот вариант. Не расширяй ответ фактами о других вариантах, если клиент явно не просит сравнение или смену варианта.",
                    "Если grounding_evidence содержит факты о нескольких вариантах, выбирай только те, которые отвечают на текущую реплику с учётом conversation; не перечисляй остальные просто потому, что они доступны.",
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

        recorded_call = False
        try:
            raw = self.client.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=self.temperature,
                response_format={"type": "json_object"},
            )
            self._record_llm_call("final_response", capture_model_input("respond", json.loads(user_prompt)))
            recorded_call = True
            parsed: dict[str, Any] = json.loads(raw)
        except Exception as exc:
            if not recorded_call:
                self._record_llm_call("final_response", capture_model_input("respond", json.loads(user_prompt)))
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

    def _normalize_prompt_reply(self, parsed: object) -> dict:
        return validate_final_response(parsed)

    def _sanitize_customer_text(self, text: str) -> str:
        return clean_customer_text(text)

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
            kind = str(item.get("kind") or item.get("tool") or "").strip()
            summary = str(item.get("summary") or "").strip()
            if kind and summary:
                projected.append({"kind": kind, "summary": summary})
        return projected

    @staticmethod
    def _build_finalization_system_prompt(active_system_prompt: str, *, knowledge_mode: str) -> str:
        """Add the application-owned evidence boundary at system-message priority."""
        contract = f"""

## Контракт финализации runtime

Приложение выбрало knowledge_mode={knowledge_mode}.
Этот раздел определяет формирование финального клиентского ответа; профильный промпт выше задаёт только роль и стиль общения.

- Возвращай только JSON-объект по required_json_schema из входного пакета. Клиентский текст помещай только в response_text, не вне JSON.

- В режиме prompt_only не утверждай факты о предметной области. Допустимы только социальный ответ или необходимое уточнение.
- В режиме kb_grounded `grounding_evidence` и `tool_facts` уже прошли свои фактические границы. Это единственные источники фактических утверждений для текущего хода.
- Если переданное evidence прямо отвечает на текущий вопрос клиента, сохрани этот ответ как фактическое ядро. Перефразируй его естественно, не выходя за точный смысл и модальность переданного evidence.
- Сначала дай прямой вывод, подтверждённый переданным evidence. Используй диалог только для разрешения ссылок, коротких продолжений и выбранного клиентом предметного ограничения; не выводи, не пересчитывай, не дополняй и не выбирай фактический результат из диалога.
- Если `user_message` содержит несколько дописанных клиентом фрагментов, считай их единым практическим запросом. Ответь на общую цель, которую они образуют вместе; не своди ответ к последнему фрагменту и не отвечай на фрагменты раздельно.
- Если текущая реплика клиента выражает или сужает ограничение либо предпочтение, явно отрази его в первом предложении перед применением подтверждённых фактов. Не отвечай так, будто реплика является новым изолированным запросом.
- Сохраняй последнее явное ограничение или предпочтение клиента в последующих коротких продолжениях. Не возвращайся к более раннему варианту, если клиент не изменил ограничение и не запросил сравнение.
- Если клиент спрашивает о других требованиях, не утверждай, что других требований нет, когда `grounding_evidence` содержит релевантные условия. Перечисли подтверждённые условия; если evidence не исчерпывающее, не делай исчерпывающий вывод об отсутствии других требований.
- Не выдумывай различие, отсутствующее предварительное условие, запрет или неопределённость, которых нет в переданном evidence.
- Выбирай только evidence, относящееся к текущему вопросу; не склеивай факты механически. Не объясняй, как был получен, проверен или выбран ответ, и не описывай внутренние источники, противоречия, проверки либо ход рассуждения: клиенту сообщается только результат и подтверждённый следующий шаг.
""".strip()
        return f"{active_system_prompt.rstrip()}\n\n{contract}"
