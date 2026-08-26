from __future__ import annotations

from app.services import direct_llm as direct_llm_module
from app.services.agent_tool_loop import DirectLLMActionAgent
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


def test_next_action_returns_provider_neutral_wiki_tool_call() -> None:
    client = ActionClient('{"action":"wiki_lookup","arguments":{},"reason":"нужны факты"}')
    service = DirectLLMService(client=client, prompt_service=PromptService())

    action = service.next_action(
        text="Как записаться на тандем?",
        context={"recent_messages": []},
        tool_observations=[],
        allowed_actions=["wiki_lookup", "finish"],
        iteration=1,
    )

    assert action["action"] == "wiki_lookup"
    assert action["arguments"] == {}
    assert action["reason"] == "нужны факты"
    assert action["llm_trace"][0]["step"] == "agent_next_action"
    assert client.calls[0]["response_format"] == {"type": "json_object"}
    assert "wiki_lookup" in str(client.calls[0]["user_prompt"])


def test_next_action_does_not_allow_nested_arguments_to_replace_action_envelope() -> None:
    client = ActionClient('{"action":"wiki_lookup","arguments":{"action":"finish","reason":"nested"},"reason":"authoritative"}')
    service = DirectLLMService(client=client, prompt_service=PromptService())
    agent = DirectLLMActionAgent(service)

    action = agent.next_action(
        text="Как записаться?",
        context={"recent_messages": []},
        tool_observations=[],
        allowed_actions=["wiki_lookup", "finish"],
        iteration=1,
    )

    assert action["action"] == "wiki_lookup"
    assert action["reason"] == "authoritative"
    assert action["arguments"] == {"action": "finish", "reason": "nested"}


def test_social_finalization_keeps_model_text_but_uses_social_route() -> None:
    client = ActionClient('{"route":"cannot_answer","response_text":"Пожалуйста!","confidence":1,"reason":"model_misclassified_social"}')
    service = DirectLLMService(client=client, prompt_service=PromptService())

    result = service.respond(
        "Спасибо",
        {"grounding_status": "not_found"},
        knowledge_mode="prompt_only",
        response_intent="social_reply",
    )

    assert result["route"] == "social_reply"
    assert result["response_text"] == "Пожалуйста!"


def test_finalizer_prompt_requires_natural_grammatical_russian() -> None:
    client = ActionClient('{"route":"answer","response_text":"Готовый ответ.","confidence":1,"reason":"ready"}')
    service = DirectLLMService(client=client, prompt_service=PromptService())

    service.respond(
        "Как оформить вопрос?",
        {"grounding_status": "ready", "grounded_facts": ["Подтверждённый факт"]},
        knowledge_mode="kb_grounded",
        conversation_context={"recent_messages": []},
    )

    prompt = str(client.calls[0]["user_prompt"])
    assert "При response_intent=answer и knowledge_mode=kb_grounded подготовь готовый прямой ответ на текущий вопрос только из evidence." in prompt
    assert "Если текущая реплика прямо отвечает на предыдущий вопрос ассистента, прими её как состояние диалога и продолжи ответ; не повторяй тот же вопрос." in prompt
    assert "answer_basis — служебное краткое описание evidence, а не самостоятельный источник фактов и не требование закрыть вопрос клиента." in prompt
    assert "Если evidence не содержит прямого ответа на фактическую часть текущей реплики, не возвращай route=answer." in prompt
    assert "Верни route=clarification_requested только когда один естественный уточняющий вопрос может привести к подтверждённому ответу; иначе верни route=cannot_answer с естественным индивидуальным текстом без фактических утверждений и без шаблонной фразы." in prompt
    assert "Связанность evidence с темой вопроса не означает, что evidence отвечает на вопрос." in prompt
    assert "Если прямого ответа нет, не создавай route=answer и не задавай вопрос, который предполагает неподтверждённый факт." in prompt
    assert "В ответе допустима только редактура утверждений, уже содержащихся в evidence. Нельзя выводить новое отношение, процедуру, требование или результат из сочетания нескольких подтверждённых утверждений." in prompt
    assert "При response_intent=missing_grounding используй текущую реплику и conversation только для формы естественного ответа; не используй их для выбора, дополнения или вывода фактического содержания." in prompt
    assert "Если tool_facts содержит profile_no_answer_option, используй эту profile-declared опцию как следующий шаг и не сообщай клиенту о нехватке evidence." in prompt
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
