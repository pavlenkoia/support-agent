from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

from app.integrations.llm.base import BaseLLMClient
from app.services import direct_llm as direct_llm_module
from app.services import tool_runtime as tool_runtime_module
from app.services.direct_llm import DirectLLMService
from app.services.policy import PolicyService
from app.services.tool_runtime import ToolRuntimeService


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 7, 13, 7, 9, 21, tzinfo=tz or UTC)








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




def test_tool_runtime_projects_public_period_metadata_without_weekend_dates_or_exact_date_list(monkeypatch) -> None:
    monkeypatch.setattr(tool_runtime_module, "datetime", FrozenDateTime)
    service = ToolRuntimeService()

    result = service.collect(text="Можно прыгнуть в августе?", kb_hits=[])

    assert result["tool_status"] == "used"
    assert result["tool_results"][0]["kind"] == "calendar_period_weekends"
    assert "01.08.2026" in result["tool_results"][0]["summary"]
    assert "Эти даты являются только календарными ориентирами." in result["tool_results"][0]["summary"]
    public = service.project_public_period_observation(result["tool_results"][0])
    assert public["kind"] == "calendar_period_public"
    assert public["structured"]["original_period"] == "в августе"
    assert public["structured"]["start_month"] == 8
    assert public["structured"]["end_month"] == 8
    assert "weekend_dates" not in public["structured"]
    assert "exact_dates" not in public["structured"]
    assert "01.08.2026" not in json.dumps(public, ensure_ascii=False)
    assert "2026-08-01" not in json.dumps(public, ensure_ascii=False)


def test_tool_runtime_public_period_projection_preserves_single_date_weekday_observation(monkeypatch) -> None:
    monkeypatch.setattr(tool_runtime_module, "datetime", FrozenDateTime)
    service = ToolRuntimeService()

    result = service.collect(text="Можно ли прыгнуть 25 июня?", kb_hits=[])

    assert result["tool_results"][0]["kind"] == "calendar_weekday"
    projected = service.project_public_period_observation(result["tool_results"][0])
    assert projected == result["tool_results"][0]


def test_ready_grounding_does_not_trigger_application_side_answer_override(monkeypatch) -> None:
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

    assert result["route"] == "clarification_requested"
    assert result["response_text"] == "Какой именно вариант услуги вас интересует?"
    assert result["reason"] == "model_requested_clarification"

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


def test_direct_llm_runtime_error_fails_closed_without_customer_reply(monkeypatch) -> None:
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

    assert result["route"] == "retry_pending"
    assert result["response_text"] == ""
    assert result["reason"] == "final_response_error:RuntimeError"
    assert result["llm_trace"][0]["step"] == "final_response_failed_closed"




def test_direct_llm_runtime_error_does_not_emit_kb_answer_basis_verbatim(monkeypatch) -> None:
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

    assert result["route"] == "retry_pending"
    assert result["response_text"] == ""
    assert result["reason"] == "final_response_error:RuntimeError"


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






def test_direct_llm_period_failure_does_not_emit_application_written_reply(monkeypatch) -> None:
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

    assert result["route"] == "retry_pending"
    assert result["response_text"] == ""
    assert result["reason"] == "final_response_error:AssertionError"
