from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

from app.integrations.llm.base import BaseLLMClient
from app.services import direct_llm as direct_llm_module
from app.services import tool_runtime as tool_runtime_module
from app.services.direct_llm import DirectLLMService
from app.services.orchestrator import OrchestratorService
from app.services.policy import PolicyService
from app.services.tool_runtime import ToolRuntimeService


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 7, 13, 7, 9, 21, tzinfo=tz or UTC)


def test_planner_receives_closed_runtime_capability_set() -> None:
    class CapturingClient(BaseLLMClient):
        def __init__(self) -> None:
            self.payload: dict | None = None

        def generate(self, **kwargs):
            self.payload = json.loads(kwargs["user_prompt"])
            return json.dumps(
                {
                    "action": "read_kb",
                    "scope_status": "in_scope",
                    "confidence": 1.0,
                    "reason": "test",
                    "clarification_question": "",
                }
            )

    client = CapturingClient()
    result = DirectLLMService(client=client).assess_request(
        "Не могу дозвониться",
        retrieval={"kb_status": "not_started", "kb_snippets": []},
        runtime_capabilities=ToolRuntimeService().planner_capabilities(),
    )

    assert result["action"] == "read_kb"
    assert client.payload is not None
    assert client.payload["runtime_capabilities"] == [
        {
            "name": "calendar",
            "supports": "weekday and calendar-period calculations for dates explicitly present in the customer turn",
        }
    ]
    assert any("no other tool" in rule for rule in client.payload["rules"])


def test_planner_accepts_answer_from_prompt_when_system_prompt_is_sufficient() -> None:
    class PromptSufficientClient(BaseLLMClient):
        def generate(self, **kwargs):
            payload = json.loads(kwargs["user_prompt"])
            assert "answer_from_prompt" in payload["required_json_schema"]["action"]
            assert any("system prompt" in rule.lower() for rule in payload["rules"])
            return json.dumps(
                {
                    "action": "answer_from_prompt",
                    "scope_status": "in_scope",
                    "confidence": 1.0,
                    "reason": "system_prompt_is_sufficient",
                    "clarification_question": "",
                }
            )

    result = DirectLLMService(client=PromptSufficientClient()).assess_request(
        "Можно использовать сертификат в другом городе?",
        retrieval={"kb_status": "not_started", "kb_snippets": []},
        runtime_capabilities=[],
    )

    assert result["action"] == "answer_from_prompt"


def test_orchestrator_answer_from_prompt_skips_retrieval_and_kb_agent() -> None:
    class NoRetrieval:
        def retrieve(self, *args, **kwargs):
            raise AssertionError("KB retrieval must not run for answer_from_prompt")

    class NoKBAgent:
        def read(self, *args, **kwargs):
            raise AssertionError("KB agent must not run for answer_from_prompt")

    class PromptDirect:
        def assess_request(self, *args, **kwargs):
            return {
                "action": "answer_from_prompt",
                "scope_status": "in_scope",
                "confidence": 1.0,
                "reason": "prompt_sufficient",
            }

        def respond(self, text, kb_result, **kwargs):
            assert text == "Можно использовать сертификат в другом городе?"
            assert kb_result["kb_status"] == "not_started"
            assert kb_result["grounding_status"] == "not_required"
            return {
                "route": "answer",
                "response_text": "Сертификат можно использовать только в Челябинске.",
                "confidence": 1.0,
                "reason": "prompt_finalized",
                "llm_trace": [],
            }

    class NoTools:
        def matches_calendar_query(self, text):
            return False

        def planner_capabilities(self):
            return []

    result = OrchestratorService(
        retrieval=cast(Any, NoRetrieval()),
        kb_agent=cast(Any, NoKBAgent()),
        direct_llm=cast(Any, PromptDirect()),
        policy=PolicyService(),
        tool_runtime=cast(Any, NoTools()),
    ).run(
        text="Можно использовать сертификат в другом городе?",
        context={"recent_messages": []},
        knowledge_backend="filesystem",
        knowledge_root="/tmp/unused",
        knowledge_query="Можно использовать сертификат в другом городе?",
    )

    assert result["route"]["route"] == "answer"
    assert result["response_strategy"]["steps"] == ["answer"]


def test_prompt_only_finalizer_uses_system_prompt_without_grounding_evidence(monkeypatch) -> None:
    class PromptOnlyClient(BaseLLMClient):
        def __init__(self) -> None:
            self.payload: dict | None = None

        def generate(self, **kwargs):
            self.payload = json.loads(kwargs["user_prompt"])
            return json.dumps(
                {
                    "route": "answer",
                    "response_text": "Сертификат можно использовать только в Челябинске.",
                    "confidence": 1.0,
                    "reason": "prompt_only_finalized",
                }
            )

    client = PromptOnlyClient()
    monkeypatch.setattr(direct_llm_module.settings, "direct_llm_provider", "mistral")
    result = DirectLLMService(client=client).respond(
        "Можно использовать сертификат в другом городе?",
        {
            "kb_status": "not_started",
            "grounding_status": "not_required",
            "answer_basis": "",
            "grounded_facts": [],
        },
        knowledge_mode="prompt_only",
        conversation_context={"recent_messages": []},
    )

    assert client.payload is not None
    assert client.payload["knowledge_mode"] == "prompt_only"
    assert client.payload["grounding_evidence"] == {"answer_basis": "", "facts": []}
    assert "planner_action" not in json.dumps(client.payload, ensure_ascii=False)
    assert "planner_reason" not in json.dumps(client.payload, ensure_ascii=False)
    assert result["response_text"] == "Сертификат можно использовать только в Челябинске."


def test_tool_runtime_resolves_relative_dates_from_runtime_context(monkeypatch) -> None:
    monkeypatch.setattr(tool_runtime_module, "datetime", FrozenDateTime)
    service = ToolRuntimeService()

    cases = [
        ("Можно сегодня заехать в офис к 15:00 за сертификатом?", "2026-07-13"),
        ("Офис сегодня работает?", "2026-07-13"),
        ("Можно ли прыгнуть сегодня?", "2026-07-13"),
        ("Можно ли завтра?", "2026-07-14"),
        ("Можно ли послезавтра?", "2026-07-15"),
    ]

    for text, expected_iso_date in cases:
        result = service.collect(text=text, kb_hits=[])
        assert result["tool_status"] == "used"
        assert result["tool_trace"][0]["tool_name"] == "calendar_weekday"
        assert result["tool_trace"][0]["input"]["iso_date"] == expected_iso_date


def test_tool_runtime_does_not_treat_office_hours_as_weekend_jump_check(monkeypatch) -> None:
    monkeypatch.setattr(tool_runtime_module, "datetime", FrozenDateTime)
    service = ToolRuntimeService()

    result = service.collect(
        text="Можно сегодня заехать в офис к 15 часам для приобретения подарочного сертификата?",
        kb_hits=[
            {"text": "Подарочные сертификаты можно купить онлайн и в офисе по будням."},
            {"text": "Прыжки обычно проходят по выходным."},
        ],
    )

    assert result["tool_trace"][0]["input"]["iso_date"] == "2026-07-13"
    assert [item["kind"] for item in result["tool_results"]] == ["calendar_weekday"]


def test_direct_llm_grounded_fallback_ignores_weekend_kb_for_office_question() -> None:
    service = DirectLLMService(client=None)

    result = service._fallback_answer_from_grounding(
        text="Можно сегодня заехать в офис к 15 часам для приобретения подарочного сертификата?",
        answer_context=[
            {"text": "Подарочный сертификат можно купить онлайн на сайте или в офисе по будням с 09:00 до 17:00."},
            {"text": "Прыжки обычно проходят по выходным."},
        ],
        kb_hits=[],
        conversation_context={
            "tool_observations": [
                {
                    "kind": "calendar_weekday",
                    "summary": "Дата 2026-07-13 приходится на понедельник.",
                    "structured": {"weekday_ru": "понедельник", "is_weekend": False},
                }
            ]
        },
        reason="test_fallback",
    )

    assert result is not None
    assert "Прыжки обычно проходят по выходным" not in result["response_text"]
    assert "офисе по будням" in result["response_text"]


def test_ready_grounding_cannot_finish_as_clarification(monkeypatch) -> None:
    class ClarifyingClient:
        def generate(self, **kwargs):
            return '{"route":"clarification_requested","response_text":"Какой именно вариант услуги вас интересует?","confidence":0.9,"reason":"model_requested_clarification"}'

    monkeypatch.setattr(direct_llm_module.settings, "direct_llm_provider", "mistral")
    result = DirectLLMService(client=ClarifyingClient()).respond(
        "Можно записаться на следующую неделю?",
        {
            "kb_status": "found",
            "grounding_status": "ready",
            "answer_basis": "Прыжки обычно проходят по выходным и зависят от погоды и анонсов. Записаться можно по телефону 214-30-30: добавочный 1 для самостоятельного прыжка и добавочный 2 для тандема.",
            "grounded_facts": [
                "Прыжки обычно проходят по выходным.",
                "Проведение зависит от погоды и анонсов.",
                "Записаться можно по телефону 214-30-30: добавочный 1 для самостоятельного прыжка и добавочный 2 для тандема.",
            ],
        },
        conversation_context={"recent_messages": [{"role": "user", "content": "Можно записаться на следующую неделю?"}]},
    )

    assert result["route"] == "answer"
    assert "Прыжки обычно проходят по выходным" in result["response_text"]
    assert "Какой именно вариант" not in result["response_text"]


def test_ready_grounding_is_finalized_by_customer_facing_model(monkeypatch) -> None:
    class ContextAwareClient:
        def generate(self, **kwargs):
            return '{"route":"answer","response_text":"Офис работает по будням с 09:00 до 17:00.","confidence":0.9,"reason":"contextual_answer"}'

    monkeypatch.setattr(direct_llm_module.settings, "direct_llm_provider", "mistral")
    result = DirectLLMService(client=ContextAwareClient()).respond(
        "Дозвониться не могу (",
        {
            "kb_status": "found",
            "grounding_status": "ready",
            "answer_basis": "Офис работает по будням с 09:00 до 17:00. Перед визитом рекомендовано звонить.",
            "grounded_facts": ["Офис работает по будням с 09:00 до 17:00."],
        },
        conversation_context={"recent_messages": [
            {"role": "user", "content": "А сегодня офис работает?"},
            {"role": "assistant", "content": "Сегодня офис уже не работает."},
            {"role": "user", "content": "Дозвониться не могу ("},
        ]},
    )

    assert result["route"] == "answer"
    assert result["response_text"] == "Офис работает по будням с 09:00 до 17:00."
    assert result["reason"] == "contextual_answer"


def test_ready_grounding_sends_answer_basis_and_facts_without_internal_trace_to_final_model(monkeypatch) -> None:
    class CapturingClient(BaseLLMClient):
        def __init__(self) -> None:
            self.payload: dict | None = None
            self.system_prompt: str | None = None

        def generate(self, **kwargs):
            self.system_prompt = kwargs["system_prompt"]
            self.payload = json.loads(kwargs["user_prompt"])
            return (
                '{"route":"answer","response_text":"Завтра прыжки не проводятся. '
                'Ближайшие прыжки обычно проходят в выходные.","confidence":0.9,"reason":"finalized"}'
            )

    client = CapturingClient()
    monkeypatch.setattr(direct_llm_module.settings, "direct_llm_provider", "mistral")
    service = DirectLLMService(client=client)
    result = service.respond(
        "Можно ли завтра прыгнуть с парашютом?",
        {
            "kb_status": "found",
            "grounding_status": "ready",
            "answer_basis": "В этот день прыжки не проводятся.",
            "grounded_facts": [
                "Прыжки обычно проходят по выходным.",
                "Дата 2026-08-04 — вторник, будний день.",
            ],
            "answer_context": [{"text": "Прыжки обычно проходят по выходным и зависят от погоды и анонса."}],
            "trace": {
                "navigation": {"reason": "Внутреннее обоснование выбора страницы."},
                "review": {"reason": "Внутренняя проверка покрытия."},
            },
        },
        conversation_context={
            "recent_messages": [
                {"role": "user", "content": "А в субботу можно?"},
                {"role": "assistant", "content": "Прыжки обычно проходят по выходным."},
                {"role": "user", "content": "А завтра?"},
            ]
        },
        tool_observations=[{"kind": "calendar_weekday", "summary": "Дата 2026-08-04 приходится на вторник."}],
    )

    assert result["route"] == "answer"
    assert client.payload is not None
    assert client.system_prompt is not None
    evidence = client.payload["grounding_evidence"]
    assert evidence["answer_basis"] == "В этот день прыжки не проводятся."
    assert evidence["facts"] == [
        "Прыжки обычно проходят по выходным.",
        "Дата 2026-08-04 — вторник, будний день.",
    ]
    assert "trace" not in json.dumps(client.payload, ensure_ascii=False)
    assert result["response_text"] == "Завтра прыжки не проводятся. Ближайшие прыжки обычно проходят в выходные."
    assert result["reason"] == "finalized"


def test_direct_llm_runtime_error_returns_grounded_kb_answer(monkeypatch) -> None:
    class BrokenClient:
        def generate(self, **kwargs):
            raise RuntimeError("IncompleteRead")

    monkeypatch.setattr(direct_llm_module.settings, "direct_llm_provider", "mistral")
    result = DirectLLMService(client=BrokenClient()).respond(
        "Можно ли записаться на самостоятельный прыжок?",
        {
            "kb_status": "found",
            "grounding_status": "ready",
            "answer_context": [
                {
                    "text": (
                        "На самостоятельный прыжок можно записаться по телефону +7 (351) 214-30-30, "
                        "добавочный 1. Прыжки обычно проходят по выходным; точную дату уточняйте по телефону."
                    ),
                    "source_ref": "contacts.md",
                }
            ],
        },
    )

    assert result["route"] == "answer"
    assert "+7 (351) 214-30-30" in result["response_text"]
    assert result["reason"] == "prompt_runtime_grounded_fallback:RuntimeError"


def test_direct_llm_ready_grounding_never_degrades_to_cannot_answer_when_secondary_fallback_fails(monkeypatch) -> None:
    class BrokenClient:
        def generate(self, **kwargs):
            raise RuntimeError("IncompleteRead")

    monkeypatch.setattr(direct_llm_module.settings, "direct_llm_provider", "mistral")
    service = DirectLLMService(client=BrokenClient())
    monkeypatch.setattr(service, "_fallback_answer_from_grounding", lambda *args, **kwargs: None)

    result = service.respond(
        "Можно ли в тандеме при весе 120 кг?",
        {
            "kb_status": "found",
            "grounding_status": "ready",
            "grounded_facts": [
                "Максимальный вес для тандем-прыжка — до 85 кг.",
                "При превышении веса необходимо уточнить возможность участия в офисе.",
            ],
            "answer_basis": "При весе 120 кг тандем-прыжок невозможен.",
        },
    )

    assert result["route"] == "answer"
    assert result["response_text"] == "При весе 120 кг тандем-прыжок невозможен."
    assert result["reason"] == "prompt_runtime_grounded_fallback:RuntimeError"
    assert result["llm_trace"][0]["step"] == "grounded_fallback"


def test_direct_llm_runtime_error_prefers_kb_answer_basis(monkeypatch) -> None:
    class BrokenClient:
        def generate(self, **kwargs):
            raise RuntimeError("IncompleteRead")

    monkeypatch.setattr(direct_llm_module.settings, "direct_llm_provider", "mistral")
    result = DirectLLMService(client=BrokenClient()).respond(
        "Добрый день, можно ли записаться на самостоятельный прыжок",
        {
            "kb_status": "found",
            "grounding_status": "ready",
            "answer_context": [
                {
                    "text": (
                        "Самостоятельный прыжок требует подготовки 3–4 часа в день прыжка. "
                        "Если клиент спрашивает, можно ли прыгнуть завтра, нужно уточнить анонс. "
                        "Прыжки обычно проходят по выходным."
                    ),
                    "source_ref": "skydiving-services.md",
                }
            ],
            "grounded_facts": [
                "На самостоятельный прыжок можно записаться по телефону +7 (351) 214-30-30, добавочный 1.",
                "Обычно запись проходит в пятницу после 12:00 на субботу и в субботу после 12:00 на воскресенье.",
            ],
            "answer_basis": "На самостоятельный прыжок можно записаться по телефону +7 (351) 214-30-30, добавочный 1.",
        },
    )

    assert result["route"] == "answer"
    assert "+7 (351) 214-30-30" in result["response_text"]
    assert "пятницу после 12:00" not in result["response_text"]
    assert "3–4 часа" not in result["response_text"]
    assert "прыгнуть завтра" not in result["response_text"]


def test_tool_runtime_resolves_month_range_to_weekend_dates(monkeypatch) -> None:
    monkeypatch.setattr(tool_runtime_module, "datetime", FrozenDateTime)
    service = ToolRuntimeService()

    result = service.collect(
        text="Какие выходные с мая по июнь бывают для прыжков?",
        kb_hits=[{"text": "Прыжки обычно проходят по выходным."}],
    )

    assert result["tool_status"] == "used"
    period = next(item for item in result["tool_results"] if item["kind"] == "calendar_period_weekends")
    assert period["structured"]["original_period"] == "с мая по июнь"
    assert period["structured"]["start_month"] == 5
    assert period["structured"]["end_month"] == 6
    assert period["structured"]["weekend_dates"][0] == "2026-05-02"
    assert period["structured"]["weekend_dates"][-1] == "2026-06-28"


def test_tool_runtime_resolves_month_list_and_season(monkeypatch) -> None:
    monkeypatch.setattr(tool_runtime_module, "datetime", FrozenDateTime)
    service = ToolRuntimeService()

    month_list = service.collect(text="В мае, июне и июле какие выходные?", kb_hits=[])
    season = service.collect(text="Какие выходные летом?", kb_hits=[])

    month_list_period = next(item for item in month_list["tool_results"] if item["kind"] == "calendar_period_weekends")
    season_period = next(item for item in season["tool_results"] if item["kind"] == "calendar_period_weekends")
    assert month_list_period["structured"]["start_month"] == 5
    assert month_list_period["structured"]["end_month"] == 7
    assert season_period["structured"]["start_month"] == 6
    assert season_period["structured"]["end_month"] == 8


def test_tool_runtime_normalizes_month_list_without_trailing_request_clause(monkeypatch) -> None:
    monkeypatch.setattr(tool_runtime_module, "datetime", FrozenDateTime)
    result = ToolRuntimeService().collect(
        text="Будут прыжки в тандеме в августе, сентябре и если известны даты, чтобы запланировать",
        kb_hits=[],
    )

    period = next(item for item in result["tool_results"] if item["kind"] == "calendar_period_weekends")
    assert period["structured"]["original_period"] == "в августе и сентябре"


def test_direct_llm_period_fallback_preserves_requested_period_and_adds_conditions() -> None:
    service = DirectLLMService(client=None)

    result = service._fallback_answer_from_grounding(
        text="А с мая по сентябрь?",
        answer_context=[{"text": "Прыжки обычно проходят по выходным."}],
        kb_hits=[],
        conversation_context={
            "recent_messages": [
                {"role": "user", "content": "Когда обычно проходят прыжки?"},
                {"role": "assistant", "content": "Обычно по выходным."},
            ],
            "tool_observations": [
                {
                    "kind": "calendar_period_weekends",
                    "structured": {
                        "original_period": "с мая по сентябрь",
                        "weekend_dates": ["2026-05-02", "2026-05-03"],
                    },
                }
            ]
        },
        reason="planner_requested_but_no_tool_match",
    )

    assert result is not None
    assert "с мая по сентябрь" in result["response_text"]
    assert "после этих месяцев" not in result["response_text"].lower()
    assert "анонс" in result["response_text"].lower()
    assert "погод" in result["response_text"].lower()


def test_direct_llm_period_fallback_does_not_duplicate_preposition() -> None:
    service = DirectLLMService(client=None)
    result = service._fallback_answer_from_grounding(
        text="Будут прыжки в тандеме в августе и сентябре?",
        answer_context=[{"text": "Прыжки обычно проходят по выходным."}],
        kb_hits=[],
        conversation_context={
            "tool_observations": [
                {"kind": "calendar_period_weekends", "structured": {"original_period": "в августе и сентябре"}},
            ]
        },
        reason="calendar_period_tool_guard",
    )

    assert result is not None
    assert result["response_text"].startswith("В августе и сентябре")
    assert "В период в августе" not in result["response_text"]


def test_direct_llm_period_guard_keeps_calendar_dates_out_of_customer_reply(monkeypatch) -> None:
    class IncorrectCalendarClient:
        def generate(self, **kwargs):
            raise AssertionError("calendar-period response must not delegate dates to the model")

    from app.services import direct_llm as direct_llm_module

    monkeypatch.setattr(direct_llm_module.settings, "direct_llm_provider", "mistral")
    service = DirectLLMService(client=IncorrectCalendarClient())
    result = service.respond(
        "Когда обычно проходят прыжки с мая по июню?",
        {"answer_context": [{"text": "Прыжки обычно проходят по выходным."}]},
        conversation_context={
            "tool_observations": [
                {
                    "kind": "calendar_period_weekends",
                    "structured": {
                        "original_period": "с мая по июню",
                        "weekend_dates": ["2026-05-02", "2026-05-03"],
                    },
                }
            ]
        },
        tool_observations=[
            {
                "kind": "calendar_period_weekends",
                "structured": {
                    "original_period": "с мая по июню",
                    "weekend_dates": ["2026-05-02", "2026-05-03"],
                },
            }
        ],
    )

    assert "02.05.2026" not in result["response_text"]
    assert "03.05.2026" not in result["response_text"]
    assert "анонс" in result["response_text"].lower()
    assert "погод" in result["response_text"].lower()
