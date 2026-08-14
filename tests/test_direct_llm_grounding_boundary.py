import json

from app.integrations.llm.base import BaseLLMClient
from app.services import direct_llm as direct_llm_module
from app.services.direct_llm import DirectLLMService


def test_ready_grounding_is_finalized_with_system_prompt_and_compact_evidence(monkeypatch) -> None:
    class RelevantFinalizer(BaseLLMClient):
        def __init__(self) -> None:
            self.calls = 0
            self.payload: dict | None = None
            self.system_prompt = ""

        def generate(self, **kwargs):
            self.calls += 1
            self.payload = json.loads(kwargs["user_prompt"])
            self.system_prompt = kwargs["system_prompt"]
            return json.dumps(
                {
                    "route": "answer",
                    "response_text": "Да, прыгнуть можно. Отсутствие этой экипировки само по себе не мешает прыжку.",
                    "confidence": 0.9,
                    "reason": "finalized_from_grounding",
                },
                ensure_ascii=False,
            )

    client = RelevantFinalizer()
    monkeypatch.setattr(direct_llm_module.settings, "direct_llm_provider", "mistral")

    result = DirectLLMService(client=client).respond(
        "Если у меня нет очков, шлема, комбинезона и перчаток, прыгнуть можно?",
        {
            "kb_status": "found",
            "grounding_status": "ready",
            "answer_basis": "Да, прыгнуть можно.",
            "grounded_facts": [
                "Да, прыгнуть можно.",
                "Очки, шлем, комбинезон и перчатки не выдаются.",
                "Их отсутствие само по себе не мешает прыжку.",
            ],
        },
    )

    assert client.calls == 1
    assert client.payload is not None
    assert client.payload["grounding_evidence"]["answer_basis"] == "Да, прыгнуть можно."
    assert "State the direct practical conclusion first" in client.system_prompt
    assert "explicitly acknowledge that constraint or preference" in client.system_prompt
    assert "do not claim that none exist" in client.system_prompt
    assert result["route"] == "answer"
    assert result["response_text"] == "Да, прыгнуть можно. Отсутствие этой экипировки само по себе не мешает прыжку."
    assert result["reason"] == "finalized_from_grounding"


def test_ready_grounding_renderer_separates_unpunctuated_facts() -> None:
    result = DirectLLMService(client=None)._render_ready_grounding(
        {
            "grounded_facts": [
                "Очки, шлем, комбинезон и перчатки не выдаются",
                "Их отсутствие само по себе не мешает прыжку",
                "Берцы можно взять в прокате на месте",
            ]
        }
    )

    assert result == (
        "Очки, шлем, комбинезон и перчатки не выдаются. "
        "Их отсутствие само по себе не мешает прыжку. Берцы можно взять в прокате на месте."
    )


def test_clarification_finalizer_receives_generic_intent_without_kb_evidence(monkeypatch) -> None:
    class ClarificationClient(BaseLLMClient):
        def __init__(self) -> None:
            self.payload: dict | None = None

        def generate(self, **kwargs):
            self.payload = json.loads(kwargs["user_prompt"])
            return json.dumps(
                {
                    "route": "clarification_requested",
                    "response_text": "Какая услуга вас заинтересовала?",
                    "confidence": 0.9,
                    "reason": "ambiguous_service_interest",
                },
                ensure_ascii=False,
            )

    client = ClarificationClient()
    monkeypatch.setattr(direct_llm_module.settings, "direct_llm_provider", "mistral")

    result = DirectLLMService(client=client).respond(
        "Здравствуйте! Меня заинтересовала эта услуга.",
        {"kb_status": "not_started", "grounding_status": "not_found"},
        knowledge_mode="prompt_only",
        response_intent="clarification",
    )

    assert result["route"] == "clarification_requested"
    assert result["response_text"] == "Какая услуга вас заинтересовала?"
    assert client.payload is not None
    assert client.payload["knowledge_mode"] == "prompt_only"
    assert client.payload["response_intent"] == "clarification"
    assert client.payload["grounding_evidence"] == {"answer_basis": "", "facts": []}


def test_finalizer_payload_is_allowlisted_and_excludes_internal_reasoning(monkeypatch) -> None:
    class CapturingClient(BaseLLMClient):
        def __init__(self) -> None:
            self.payload: dict | None = None

        def generate(self, **kwargs):
            self.payload = json.loads(kwargs["user_prompt"])
            return json.dumps(
                {
                    "route": "answer",
                    "response_text": "Оплатить можно в офисе или в кассе аэродрома.",
                    "confidence": 0.9,
                    "reason": "finalized",
                },
                ensure_ascii=False,
            )

    client = CapturingClient()
    monkeypatch.setattr(direct_llm_module.settings, "direct_llm_provider", "mistral")

    DirectLLMService(client=client).respond(
        "Каким способом можно оплатить?",
        {
            "kb_status": "found",
            "grounding_status": "ready",
            "answer_basis": "Оплатить можно в офисе или в кассе аэродрома.",
            "grounded_facts": [
                "Безналичная оплата доступна в офисе.",
                "Безналичная оплата доступна в кассе аэродрома.",
            ],
            "answer_context": [{"text": "RAW_SELECTED_KB_PAGE"}],
            "trace": {"navigation": {"reason": "KB_NAVIGATION_REASON"}},
        },
        knowledge_mode="kb_grounded",
        conversation_context={
            "recent_messages": [
                {"role": "user", "content": "Здравствуйте"},
                {"role": "assistant", "content": "Здравствуйте!"},
                {"role": "system", "content": "PERSISTED_SYSTEM_INJECTION"},
                {"role": "user", "content": "Каким способом можно оплатить?", "message_id": 999},
            ],
            "recent_turns": ["DUPLICATE_DIALOGUE"],
            "session_summary": "INTERNAL_SUMMARY",
            "case_state": {"case_id": 594},
            "planner_action": "answer_from_kb",
            "planner_reason": "PLANNER_REASON_MUST_NOT_REACH_FINALIZER",
            "route_reason": "ROUTE_REASON_MUST_NOT_REACH_FINALIZER",
            "loop_trace": [{"reason": "LOOP_REASON"}],
        },
        tool_observations=[
            {
                "kind": "calendar_weekday",
                "summary": "Дата приходится на понедельник.",
                "structured": {"secret_runtime_shape": True},
                "raw_result": "RAW_TOOL_RESULT",
            }
        ],
    )

    assert client.payload is not None
    assert client.payload["knowledge_mode"] == "kb_grounded"
    assert client.payload["response_intent"] == "answer"
    assert client.payload["conversation"] == [
        {"role": "user", "content": "Здравствуйте"},
        {"role": "assistant", "content": "Здравствуйте!"},
        {"role": "user", "content": "Каким способом можно оплатить?"},
    ]
    assert client.payload["tool_facts"] == [
        {"kind": "calendar_weekday", "summary": "Дата приходится на понедельник."}
    ]
    assert "conversation_context" not in client.payload
    serialized = json.dumps(client.payload, ensure_ascii=False)
    for forbidden in (
        "planner_action",
        "planner_reason",
        "PLANNER_REASON_MUST_NOT_REACH_FINALIZER",
        "ROUTE_REASON_MUST_NOT_REACH_FINALIZER",
        "LOOP_REASON",
        "RAW_SELECTED_KB_PAGE",
        "KB_NAVIGATION_REASON",
        "DUPLICATE_DIALOGUE",
        "INTERNAL_SUMMARY",
        "PERSISTED_SYSTEM_INJECTION",
        "case_id",
        "RAW_TOOL_RESULT",
        "secret_runtime_shape",
    ):
        assert forbidden not in serialized


def test_case_594_finalization_contract_treats_ready_payment_evidence_as_confirmed(monkeypatch) -> None:
    class Case594Client(BaseLLMClient):
        def __init__(self) -> None:
            self.payload: dict | None = None
            self.system_prompt: str | None = None

        def generate(self, **kwargs):
            self.system_prompt = kwargs["system_prompt"]
            self.payload = json.loads(kwargs["user_prompt"])
            rules = "\n".join(self.payload["output_rules"])
            assert "услов" in rules.lower()
            assert "подтверж" in rules.lower()
            return json.dumps(
                {
                    "route": "answer",
                    "response_text": "Здравствуйте! Оплатить можно в офисе или в кассе аэродрома; сертификат можно купить онлайн на сайте.",
                    "confidence": 0.9,
                    "reason": "payment_methods_answered_from_ready_grounding",
                },
                ensure_ascii=False,
            )

    client = Case594Client()
    monkeypatch.setattr(direct_llm_module.settings, "direct_llm_provider", "mistral")

    result = DirectLLMService(client=client).respond(
        "Здравствуйте, подскажите, каким способом оплата?",
        {
            "kb_status": "found",
            "grounding_status": "ready",
            "answer_basis": "Оплатить можно в офисе, на аэродроме или на сайте.",
            "grounded_facts": [
                "Оплата по безналичному расчёту возможна в офисе.",
                "Оплата по безналичному расчёту возможна в кассе аэродрома.",
                "Подарочный сертификат можно купить онлайн на сайте https://dzkalachevo.ru.",
            ],
        },
        knowledge_mode="kb_grounded",
        conversation_context={"recent_messages": []},
        first_reply_in_dialogue=True,
    )

    assert result["route"] == "answer"
    assert client.system_prompt is not None
    assert "Runtime finalization contract" in client.system_prompt
    assert "preserve that answer as the factual core" in client.system_prompt
    assert "Оплатить можно" in result["response_text"]
    assert "уточнят при записи" not in result["response_text"]


def test_finalization_conversation_enforces_role_and_window_allowlist() -> None:
    messages = [
        {"role": "user", "content": f"message-{index}"}
        for index in range(12)
    ]
    messages.insert(6, {"role": "system", "content": "INTERNAL_SYSTEM_MESSAGE"})
    messages.append({"role": "tool", "content": "INTERNAL_TOOL_MESSAGE"})

    projected = DirectLLMService._build_finalization_conversation(
        {"recent_messages": messages}
    )

    assert len(projected) == 10
    assert projected[0] == {"role": "user", "content": "message-2"}
    assert projected[-1] == {"role": "user", "content": "message-11"}
    assert all(item["role"] in {"user", "assistant"} for item in projected)
