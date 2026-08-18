from __future__ import annotations

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
    assert "Сформулируй клиентский текст только из подтверждённого evidence. Не принимай самостоятельное решение по вопросу клиента и не восполняй детали, которых нет в evidence." in prompt
    assert "answer_basis — служебное краткое описание evidence, а не самостоятельный источник фактов и не требование закрыть вопрос клиента." in prompt
    assert "Если evidence не содержит прямого ответа на фактическую часть текущей реплики, не возвращай route=answer." in prompt
    assert "Верни route=clarification_requested только когда один естественный уточняющий вопрос может привести к подтверждённому ответу; иначе верни route=cannot_answer с естественным индивидуальным текстом без фактических утверждений и без шаблонной фразы." in prompt
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
