from __future__ import annotations
from tests.evidence_fixtures import migrate_fixture

from app.services import direct_llm as direct_llm_module
from app.services.direct_llm import DirectLLMService


class ActionClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[dict] = []

    def generate(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        return self.response

    def get_last_call_info(self) -> dict:
        return {"provider": "test", "model": "test", "usage": {"prompt_tokens": 1}}


class PromptService:
    def load_system_prompt(self) -> str:
        return "Поведенческий контракт агента."

    def render_cannot_answer(self) -> str:
        return "Нет подтверждённых данных."


def test_direct_llm_exposes_only_active_agent_loop_entrypoints() -> None:
    service = DirectLLMService(client=ActionClient('{}'), prompt_service=PromptService())

    for legacy_method in ("answer", "assess_request", "classify_turn", "respond_social"):
        assert not hasattr(service, legacy_method)


def test_stub_provider_returns_retry_pending_without_customer_template(monkeypatch) -> None:
    monkeypatch.setattr(direct_llm_module.settings, "direct_llm_provider", "stub")
    service = DirectLLMService(prompt_service=PromptService())

    result = service.respond(
        "Вопрос",
        {"grounding_status": "not_found", "grounded_facts": []},
        knowledge_mode="prompt_only",
        response_intent="missing_grounding",
    )

    assert result["route"] == "retry_pending"
    assert result["response_text"] == ""



def test_begin_turn_requests_wiki_as_the_only_factual_source() -> None:
    client = ActionClient('{"_native_tool_calls": [{"id": "fixture-native", "type": "function", "function": {"name": "wiki_lookup", "arguments": "{\\"query\\": \\"вопрос о чате\\", \\"context_scope\\": \\"текущий вопрос\\", \\"needed_fact\\": \\"способ решения\\"}"}}]}')
    service = DirectLLMService(client=client, prompt_service=PromptService())

    result = service.begin_turn(
        text="Здравствуйте, так и не добавили в чат",
        context={"recent_messages": []},
    )

    assert result["kind"] == "wiki_lookup"
    assert "response_text" not in result
    prompt = str(client.calls[0]["user_prompt"])
    assert "calendar_lookup — не источник бизнес-фактов" in prompt
    assert "wiki_lookup — единственный источник бизнес-фактов из Wiki." in prompt
    assert "Если для ответа не хватает календарного факта" in prompt
    assert "Любой неизвестный инструмент" in prompt
    assert client.calls[0]["tool_choice"] == "auto"
    assert client.calls[0]["parallel_tool_calls"] is False
    assert "response_format" not in client.calls[0]
    assert client.calls[0]["tools"][0]["function"]["name"] == "wiki_lookup"



def test_begin_turn_accepts_provider_native_tool_call_with_valid_arguments() -> None:
    client = ActionClient(
        '{"_native_tool_calls":[{"id":"native-wiki","function":{"name":"wiki_lookup","arguments":"{\\\"query\\\":\\\"вопрос\\\",\\\"context_scope\\\":\\\"контекст\\\",\\\"needed_fact\\\":\\\"факт\\\"}"}}]}'
    )
    service = DirectLLMService(client=client, prompt_service=PromptService())

    result = service.begin_turn(text="Содержательный вопрос", context={"recent_messages": []})

    assert result["kind"] == "wiki_lookup"




def test_begin_turn_accepts_provider_tool_name_with_serialized_content_suffix_in_followup() -> None:
    client = ActionClient(
        '{"_native_tool_calls":[{"id":"native-wiki-1","function":{"name":"wiki_lookup","arguments":"{\\\"query\\\":\\\"стоимость\\\",\\\"context_scope\\\":\\\"самостоятельный вариант\\\",\\\"needed_fact\\\":\\\"цена\\\"}"}}]}'
    )
    service = DirectLLMService(client=client, prompt_service=PromptService())

    result = service.begin_turn(
        text="Сколько это стоит?",
        context={"recent_messages": [{"role": "user", "content": "У вас есть услуга?"}, {"role": "assistant", "content": "Да."}]},
    )

    assert result["kind"] == "wiki_lookup"
    assert result["tool_call_id"] == "native-wiki-1"



def test_begin_turn_does_not_guess_an_unknown_malformed_native_tool() -> None:
    client = ActionClient('{"_native_tool_calls":[{"function":{"name":"provider-garbage","arguments":"not-json-and-not-an-envelope"}}]}')
    service = DirectLLMService(client=client, prompt_service=PromptService())

    result = service.begin_turn(
        text="Сколько это стоит?",
        context={"recent_messages": [{"role": "user", "content": "У вас есть услуга?"}, {"role": "assistant", "content": "Да."}]},
    )

    assert result["kind"] == "final"
    assert result["result"]["route"] == "retry_pending"


def test_begin_turn_parses_valid_tool_envelope_before_provider_trailing_junk() -> None:
    client = ActionClient('{"_native_tool_calls": [{"id": "fixture-native", "type": "function", "function": {"name": "wiki_lookup", "arguments": "{\\"query\\": \\"вопрос\\", \\"context_scope\\": \\"контекст\\", \\"needed_fact\\": \\"факт\\"}"}}]}```json\\n{"tool_call":"wiki_lookup"}\\n```')
    service = DirectLLMService(client=client, prompt_service=PromptService())

    result = service.begin_turn(text="Содержательный вопрос", context={"recent_messages": []})

    assert result["kind"] == "wiki_lookup"


def test_finalizer_accepts_valid_response_before_provider_trailing_junk() -> None:
    client = ActionClient(
        '{"route":"answer","response_text":"Да, можно.","confidence":0.9,"reason":"grounded"}```json\n{"debug":true}\n```'
    )
    service = DirectLLMService(client=client, prompt_service=PromptService())

    result = service.respond(
        "Можно ли участвовать?",
        migrate_fixture({
            "grounding_status": "ready",
            "grounded_facts": ["Участие разрешено."],
            "answer_basis": "Участие разрешено.",
            "source_refs": ["compiled/concepts/example.md"],
        }),
        response_intent="answer",
    )

    assert result["route"] == "answer"
    assert result["response_text"] == "Да, можно."


def test_begin_turn_requests_social_finalization_without_customer_text() -> None:
    client = ActionClient('{"action":"finalize","response_intent":"social_reply","reason":"social"}')
    service = DirectLLMService(client=client, prompt_service=PromptService())

    result = service.begin_turn(text="Спасибо", context={"recent_messages": []})

    assert result["kind"] == "finalization_requested"
    assert result["response_intent"] == "social_reply"
    assert result["llm_trace"][0]["step"] == "customer_turn"


def test_begin_turn_returns_a_tool_free_social_reply_directly() -> None:
    client = ActionClient('{"route":"social_reply","response_text":"Привет! Чем могу помочь?","confidence":0.9,"reason":"pure_social"}')
    service = DirectLLMService(client=client, prompt_service=PromptService())

    result = service.begin_turn(text="Привет", context={"recent_messages": []})

    assert result["kind"] == "direct_response"
    assert result["result"]["route"] == "social_reply"
    assert result["result"]["response_text"] == "Привет! Чем могу помочь?"



def test_begin_turn_prompt_requests_action_finalize_without_client_text() -> None:
    client = ActionClient('{"action":"finalize","response_intent":"social_reply","reason":"social"}')
    service = DirectLLMService(client=client, prompt_service=PromptService())

    service.begin_turn(text="Спасибо", context={"recent_messages": []})

    prompt = str(client.calls[0]["user_prompt"])
    assert '"action": "finalize"' in prompt
    assert '"response_intent": "answer|social_reply|clarification|missing_grounding"' in prompt
    assert "Не возвращай route, response_text, confidence или клиентский черновик на этапе выбора действий." in prompt
    assert "Поля `action=finalize`" not in prompt

def test_continue_after_tool_prompt_requests_action_finalize_without_client_text() -> None:
    client = ActionClient('{"route":"answer","response_text":"Готово.","confidence":1,"reason":"done"}')
    service = DirectLLMService(client=client, prompt_service=PromptService())

    service.continue_after_tool(
        text="Вопрос",
        context={"recent_messages": []},
        tool_name="wiki_lookup",
        tool_call_id="expected-id",
        tool_request={"query": "вопрос", "context_scope": "контекст", "needed_fact": "факт"},
        observation={"status": "ready", "summary": "Факт"},
    )

    prompt = str(client.calls[0]["messages"][-1]["content"])
    assert 'action=finalize' in prompt
    assert 'response_intent=answer|social_reply|clarification|missing_grounding' in prompt
    assert "Не создавай клиентский ответ, route, response_text, confidence или черновик." in prompt


def test_malformed_json_only_records_one_provider_call() -> None:
    class MalformedClient:
        def __init__(self) -> None:
            self.calls = 0

        def generate(self, **kwargs: object) -> str:
            self.calls += 1
            _ = kwargs
            return "{not-json"

        def get_last_call_info(self) -> dict:
            return {"provider": "test", "model": "test", "duration_ms": 11, "attempts": 1, "usage": {"prompt_tokens": 1}}

    service = DirectLLMService(client=MalformedClient(), prompt_service=PromptService())

    result = service.begin_turn(text="Содержательный вопрос", context={"recent_messages": []})

    assert result["kind"] == "final"
    assert result["result"]["route"] == "retry_pending"
    assert len(result["llm_trace"]) == 1
    assert result["llm_trace"][0]["entry_kind"] == "model_call"
    assert result["llm_trace"][0]["input_packet"]["status"] == "complete"
    assert [{k: v for k, v in item.items() if k not in {"entry_kind", "input_packet"}} for item in result["llm_trace"]] == [
        {
            "role": "direct_llm",
            "step": "customer_turn",
            "provider": "test",
            "model": "test",
            "duration_ms": 11,
            "attempts": 1,
            "api_key_index": None,
            "used_failover": None,
            "failover_count": None,
            "failover_events": [],
            "usage": {"prompt_tokens": 1, "completion_tokens": None, "total_tokens": None},
            "error": None,
        }
    ]


def test_finalizer_prompt_requires_natural_grammatical_russian() -> None:
    client = ActionClient('{"route":"answer","response_text":"Готовый ответ.","confidence":1,"reason":"ready"}')
    service = DirectLLMService(client=client, prompt_service=PromptService())

    service.respond(
        "Как оформить вопрос?",
        migrate_fixture({"grounding_status": "ready", "grounded_facts": ["Подтверждённый факт"]}),
        knowledge_mode="kb_grounded",
        conversation_context={"recent_messages": []},
    )

    prompt = str(client.calls[0]["user_prompt"])
    assert "Сформулируй естественный клиентский ответ как редактор переданного evidence, выбрав route только из allowed_routes. response_intent задаёт намерение, но не доказывает достаточности сведений и не отменяет allowed_routes. Если answer разрешён и evidence прямо покрывает фактический запрос с существенными ограничениями, передай прямой ответ без дополнений и неподтверждённых выводов. Если существенных сведений нет, верни cannot_answer: клиентский текст содержит только естественно сформулированный профильный fallback из profile_no_answer_option. Не добавляй объяснение отсутствия сведений, оправдание отказа, пересказ вопроса, рассуждение, уточняющий вопрос или обещание результата. Служебные основания оставь только в reason. Для чистого календарного вопроса используй готовое календарное evidence; в смешанном запросе календарь не компенсирует непокрытую фактическую часть. Для социальной реплики создай короткий естественный social_reply без бизнес-фактов. Если текущая реплика прямо отвечает на предыдущий вопрос ассистента, прими её как состояние диалога; не повторяй тот же вопрос." in prompt
    assert "Для чистого календарного вопроса используй готовое календарное evidence" in prompt
    assert "Если текущая реплика прямо отвечает на предыдущий вопрос ассистента, прими её как состояние диалога; не повторяй тот же вопрос." in prompt
    assert "Связанность evidence с темой вопроса не означает, что evidence отвечает на вопрос." in prompt
    assert "Если прямого ответа нет, не создавай route=answer и не задавай вопрос, который предполагает неподтверждённый факт." in prompt
    assert "answer_basis — кандидатная сводка, а не самостоятельный источник фактов и не требование закрыть вопрос клиента." in prompt
    assert "Если evidence не содержит прямого ответа на фактическую часть текущей реплики, не возвращай route=answer." in prompt
    assert "allowed_routes обязательно для любого исхода. Если answer запрещён, не переоценивай отброшенные факты и кандидатную сводку и не создавай содержательный ответ. cannot_answer должен быть естественным индивидуальным текстом; фактический следующий шаг допустим только из переданной подтверждённой политики." in prompt
    assert "Отсутствие упоминания не превращай в отрицательное утверждение. При частичном покрытии ответь на подтверждённую часть и естественно обозначь границу знания; не отказывайся от всего ответа из-за одного непокрытого подпункта и не достраивай его." not in prompt
    assert "Отсутствие упоминания не превращай в отрицательное утверждение." in prompt
    assert "В ответе допустима только редактура утверждений, уже содержащихся в evidence. Нельзя выводить новое отношение, процедуру, требование или результат из сочетания нескольких подтверждённых утверждений." in prompt
    assert "Сохраняй выраженное ранее клиентом предметное ограничение или выбранный вариант" in prompt
    assert "Не расширяй ответ фактами о других вариантах" in prompt
    assert "При cannot_answer весь response_text — только профильный fallback из profile_no_answer_option, сформулированный естественно и без дополнительного содержания. Сохраняй модальность политики, не добавляй неподтверждённые контакты, условия или обещания. Объяснения, оправдания, пересказ запроса и уточняющие вопросы в response_text не допускаются." in prompt
    assert "После прямого ответа добавь только нужные клиенту подтверждённые условия, ограничения или следующий шаг." not in prompt
    assert "Не склеивай извлечённые факты механически." in prompt
    assert "Сохраняй смысловые связи между субъектом, действием, условием и способом действия." in prompt
    assert "Перед отправкой проверь, что фраза грамматически закончена и не меняет подтверждённый смысл evidence." in prompt
    assert "Приоритеты finalizer: подтверждённая фактическая точность выше полноты, полезности и стилистической гладкости ответа." in prompt
    assert "Ты редактор подтверждённого evidence, а не самостоятельный решатель вопроса клиента." in prompt
    assert "Формируй ответ только как естественную редактуру grounding_evidence и tool_facts; не закрывай непокрытую evidence часть вопроса рассуждением, догадкой или общими знаниями." in prompt
    assert "Сообщение клиента и conversation служат только для понимания контекста диалога и не подтверждают новые факты." in prompt
    assert "Каждое фактическое утверждение в response_text должно прямо следовать из grounding_evidence или tool_facts." in prompt
    assert "Перед возвратом JSON внутренне сверь каждое фактическое утверждение черновика с grounding_evidence и tool_facts; убери утверждение, которое не имеет прямого подтверждения." in prompt
    assert "Не превращай правдоподобное предположение, общий опыт модели или формулировку клиента в фактическое утверждение." in prompt
    assert "После прямого ответа не добавляй другие подтверждённые факты, если клиент прямо не спрашивал о них и они не нужны, чтобы понять этот ответ или выполнить требуемое действие." in prompt
