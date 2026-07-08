import json
from pathlib import Path

from app.integrations.llm.base import BaseLLMClient
from app.services.kb_agent import KBAgentService
from app.core.config import settings


class SequentialClient(BaseLLMClient):
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def generate(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.0, response_format=None) -> str:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "temperature": temperature,
                "response_format": response_format,
            }
        )
        return json.dumps(self.responses.pop(0), ensure_ascii=False)


class RawSequentialClient(BaseLLMClient):
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)

    def generate(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.0, response_format=None) -> str:
        _ = (system_prompt, user_prompt, temperature, response_format)
        return self.responses.pop(0)


def test_kb_agent_navigates_reviews_and_extracts_grounded_facts_from_selected_pages(tmp_path: Path) -> None:
    booking = tmp_path / "booking-and-schedule.md"
    booking.write_text("# Booking and Schedule\n\nПолет лучше согласовать заранее.\n", encoding="utf-8")
    flights = tmp_path / "flight-services.md"
    flights.write_text("# Flight Services\n\nЕсть кабина Ан-2 и салон.\n", encoding="utf-8")

    client = SequentialClient(
        [
            {
                "user_intent": "понять, как купить полет на АН-2",
                "information_needs": ["способ покупки", "что доступно по АН-2"],
                "selected_source_refs": [str(booking), str(flights)],
                "reason": "Нужны страницы про запись и услуги АН-2",
            },
            {
                "coverage_status": "enough",
                "missing_facts": [],
                "additional_source_refs": [],
                "reason": "Этих страниц достаточно",
            },
            {
                "grounding_status": "ready",
                "answer_basis": "Полет на Ан-2 лучше согласовать заранее; доступны кабина и салон.",
                "grounded_facts": [
                    "Полет лучше согласовать заранее.",
                    "Есть кабина Ан-2 и салон.",
                ],
                "missing_information": [],
                "cited_source_refs": [str(booking), str(flights)],
                "reason": "selected_pages_grounded",
            },
        ]
    )
    service = KBAgentService(client=client)
    kb_hits = [
        {
            "source_ref": str(tmp_path / "index.md"),
            "source_type": "wiki_index",
            "retrieval_mode": "llm_wiki_catalog",
            "text": "# Wiki Index\n[[booking-and-schedule]]\n[[flight-services]]",
        },
        {
            "source_ref": str(booking),
            "source_type": "wiki_page_card",
            "page_title": "Booking and Schedule",
            "linked_pages": ["flight-services"],
            "page_summary": "Запись и расписание полетов.",
            "page_preview": "Полет лучше согласовать заранее.",
            "retrieval_mode": "llm_wiki_catalog",
            "text": "# Booking and Schedule\n\n## Summary\nЗапись и расписание полетов.\n\n## Key facts\nПолет лучше согласовать заранее.",
        },
        {
            "source_ref": str(flights),
            "source_type": "wiki_page_card",
            "page_title": "Flight Services",
            "linked_pages": ["booking-and-schedule"],
            "page_summary": "АН-2, кабина и салон.",
            "page_preview": "Есть кабина Ан-2 и салон.",
            "retrieval_mode": "llm_wiki_catalog",
            "text": "# Flight Services\n\n## Summary\nАН-2, кабина и салон.\n\n## Key facts\nЕсть кабина Ан-2 и салон.",
        },
    ]

    result = service.read("Как купить полет на Ан-2?", kb_hits)

    assert result["kb_mode"] == "llm_wiki_selected_pages"
    assert result["grounding_status"] == "ready"
    assert result["source_refs"] == [str(booking), str(flights)]
    assert len(result["answer_context"]) == 2
    assert "согласовать заранее" in result["answer_context"][0]["text"]
    assert "кабина Ан-2" in result["answer_context"][1]["text"]
    assert result["grounded_facts"][0] == "Полет лучше согласовать заранее."
    assert len(client.calls) == 3

    plan_payload = json.loads(client.calls[0]["user_prompt"])
    assert plan_payload["task"] == "Wiki navigation plan before full-page reading."
    review_payload = json.loads(client.calls[1]["user_prompt"])
    assert review_payload["task"] == "Coverage review after selective full-page reading."
    extract_payload = json.loads(client.calls[2]["user_prompt"])
    assert extract_payload["task"] == "Extract grounded facts from the selected wiki pages for the support agent."
    assert len(extract_payload["selected_full_pages"]) == 2


def test_kb_agent_can_request_one_more_page_after_coverage_review(tmp_path: Path) -> None:
    booking = tmp_path / "booking-and-schedule.md"
    booking.write_text("# Booking and Schedule\n\nПолет лучше согласовать заранее.\n", encoding="utf-8")
    flights = tmp_path / "flight-services.md"
    flights.write_text("# Flight Services\n\nЕсть кабина Ан-2 и салон.\n", encoding="utf-8")
    office = tmp_path / "office-chelyabinsk.md"
    office.write_text("# Office Chelyabinsk\n\nОплатить можно в офисе по будням.\n", encoding="utf-8")

    client = SequentialClient(
        [
            {
                "user_intent": "понять, как купить полет на АН-2",
                "information_needs": ["способ покупки"],
                "selected_source_refs": [str(booking), str(flights)],
                "reason": "Сначала хватит записи и услуг",
            },
            {
                "coverage_status": "need_more_pages",
                "missing_facts": ["где оплатить"],
                "additional_source_refs": [str(office)],
                "reason": "Нужна страница офиса",
            },
            {
                "grounding_status": "ready",
                "answer_basis": "Полет лучше согласовать заранее; доступны кабина и салон; оплатить можно в офисе по будням.",
                "grounded_facts": [
                    "Полет лучше согласовать заранее.",
                    "Есть кабина Ан-2 и салон.",
                    "Оплатить можно в офисе по будням.",
                ],
                "missing_information": [],
                "cited_source_refs": [str(booking), str(flights), str(office)],
                "reason": "selected_pages_grounded",
            },
        ]
    )
    service = KBAgentService(client=client)
    kb_hits = [
        {"source_ref": str(tmp_path / "index.md"), "source_type": "wiki_index", "retrieval_mode": "llm_wiki_catalog", "text": "# Wiki Index"},
        {"source_ref": str(booking), "source_type": "wiki_page_card", "page_title": "Booking and Schedule", "linked_pages": ["flight-services", "office-chelyabinsk"], "page_summary": "Запись и расписание полетов.", "page_preview": "Полет лучше согласовать заранее.", "retrieval_mode": "llm_wiki_catalog", "text": "# Booking and Schedule"},
        {"source_ref": str(flights), "source_type": "wiki_page_card", "page_title": "Flight Services", "linked_pages": ["booking-and-schedule"], "page_summary": "АН-2, кабина и салон.", "page_preview": "Есть кабина Ан-2 и салон.", "retrieval_mode": "llm_wiki_catalog", "text": "# Flight Services"},
        {"source_ref": str(office), "source_type": "wiki_page_card", "page_title": "Office Chelyabinsk", "linked_pages": ["booking-and-schedule"], "page_summary": "Офис для оплаты.", "page_preview": "Оплатить можно в офисе по будням.", "retrieval_mode": "llm_wiki_catalog", "text": "# Office Chelyabinsk"},
    ]

    result = service.read("Как купить полет на Ан-2?", kb_hits)

    assert result["source_refs"] == [str(booking), str(flights), str(office)]
    assert len(result["answer_context"]) == 3
    assert "Оплатить можно в офисе по будням" in result["answer_context"][2]["text"]
    assert result["trace"]["review"]["coverage_status"] == "need_more_pages"


def test_kb_agent_recovers_json_object_from_markdown_fence(tmp_path: Path) -> None:
    booking = tmp_path / "booking-and-schedule.md"
    booking.write_text("# Booking and Schedule\n\nПрыжки обычно по выходным.\n", encoding="utf-8")

    client = RawSequentialClient(
        [
            "```json\n{\"user_intent\":\"расписание\",\"information_needs\":[\"когда проходят прыжки\"],\"selected_source_refs\":[\"%s\"],\"reason\":\"fenced json\"}\n```" % booking,
            "```json\n{\"coverage_status\":\"enough\",\"missing_facts\":[],\"additional_source_refs\":[],\"reason\":\"fenced json\"}\n```",
            "```json\n{\"grounding_status\":\"ready\",\"answer_basis\":\"Прыжки обычно по выходным.\",\"grounded_facts\":[\"Прыжки обычно по выходным.\"],\"missing_information\":[],\"cited_source_refs\":[\"%s\"],\"reason\":\"fenced json\"}\n```" % booking,
        ]
    )
    service = KBAgentService(client=client)
    kb_hits = [
        {"source_ref": str(tmp_path / "index.md"), "source_type": "wiki_index", "retrieval_mode": "llm_wiki_catalog", "text": "# Wiki Index"},
        {"source_ref": str(booking), "source_type": "wiki_page_card", "page_title": "Booking and Schedule", "linked_pages": [], "page_summary": "Расписание прыжков.", "page_preview": "Прыжки обычно по выходным.", "retrieval_mode": "llm_wiki_catalog", "text": "# Booking and Schedule"},
    ]

    result = service.read("Когда обычно проходят прыжки?", kb_hits)

    assert result["grounding_status"] == "ready"
    assert result["grounded_facts"] == ["Прыжки обычно по выходным."]
    assert result["trace"]["navigation"]["reason"] == "fenced json"


def test_kb_agent_can_skip_coverage_review_via_settings(tmp_path: Path) -> None:
    booking = tmp_path / "booking-and-schedule.md"
    booking.write_text("# Booking and Schedule\n\nПрыжки обычно по выходным.\n", encoding="utf-8")

    client = SequentialClient(
        [
            {
                "user_intent": "расписание",
                "information_needs": ["когда проходят прыжки"],
                "selected_source_refs": [str(booking)],
                "reason": "picked booking page",
            },
            {
                "grounding_status": "ready",
                "answer_basis": "Прыжки обычно по выходным.",
                "grounded_facts": ["Прыжки обычно по выходным."],
                "missing_information": [],
                "cited_source_refs": [str(booking)],
                "reason": "selected_pages_grounded",
            },
        ]
    )
    service = KBAgentService(client=client)
    kb_hits = [
        {"source_ref": str(tmp_path / "index.md"), "source_type": "wiki_index", "retrieval_mode": "llm_wiki_catalog", "text": "# Wiki Index"},
        {"source_ref": str(booking), "source_type": "wiki_page_card", "page_title": "Booking and Schedule", "linked_pages": [], "page_summary": "Расписание прыжков.", "page_preview": "Прыжки обычно по выходным.", "retrieval_mode": "llm_wiki_catalog", "text": "# Booking and Schedule"},
    ]

    old_value = settings.kb_agent_skip_coverage_review
    settings.kb_agent_skip_coverage_review = True
    try:
        result = service.read("Когда обычно проходят прыжки?", kb_hits)
    finally:
        settings.kb_agent_skip_coverage_review = old_value

    assert result["grounding_status"] == "ready"
    assert result["trace"]["review"]["reason"] == "skip_coverage_review:settings"
    assert len(client.calls) == 2


def test_kb_agent_can_use_deterministic_navigation_with_followup_context(tmp_path: Path) -> None:
    booking = tmp_path / "booking-and-schedule.md"
    booking.write_text("# Booking and Schedule\n\nПрыжки обычно по выходным. Для групп около 20 человек возможна договоренность на другой день.\n", encoding="utf-8")
    certificates = tmp_path / "certificates.md"
    certificates.write_text("# Certificates\n\nПодарочные сертификаты можно купить онлайн и в офисе.\n", encoding="utf-8")

    client = SequentialClient(
        [
            {
                "grounding_status": "ready",
                "answer_basis": "Прыжки обычно по выходным; для групп около 20 человек возможна договоренность на другой день.",
                "grounded_facts": [
                    "Прыжки обычно по выходным.",
                    "Для групп около 20 человек возможна договоренность на другой день.",
                ],
                "missing_information": [],
                "cited_source_refs": [str(booking)],
                "reason": "selected_pages_grounded",
            },
        ]
    )
    service = KBAgentService(client=client)
    kb_hits = [
        {"source_ref": str(tmp_path / "index.md"), "source_type": "wiki_index", "retrieval_mode": "llm_wiki_catalog", "text": "# Wiki Index"},
        {"source_ref": str(booking), "source_type": "wiki_page_card", "page_title": "Booking and Schedule", "linked_pages": [], "page_summary": "Расписание прыжков.", "page_preview": "Прыжки обычно по выходным.", "retrieval_mode": "llm_wiki_catalog", "text": "# Booking and Schedule"},
        {"source_ref": str(certificates), "source_type": "wiki_page_card", "page_title": "Certificates", "linked_pages": [], "page_summary": "Подарочные сертификаты.", "page_preview": "Подарочные сертификаты можно купить онлайн.", "retrieval_mode": "llm_wiki_catalog", "text": "# Certificates"},
    ]
    context = {
        "recent_messages": [
            {"role": "user", "content": "Прыжки обычно когда проходят?"},
            {"role": "assistant", "content": "Прыжки обычно проходят по выходным."},
        ]
    }

    old_skip = settings.kb_agent_skip_coverage_review
    old_deterministic = settings.kb_agent_deterministic_navigation
    settings.kb_agent_skip_coverage_review = True
    settings.kb_agent_deterministic_navigation = True
    try:
        result = service.read("А если в будний день?", kb_hits, conversation_context=context)
    finally:
        settings.kb_agent_skip_coverage_review = old_skip
        settings.kb_agent_deterministic_navigation = old_deterministic

    assert result["grounding_status"] == "ready"
    assert result["source_refs"] == [str(booking)]
    assert result["trace"]["navigation"]["reason"].startswith("deterministic_navigation:")
    assert result["trace"]["selected_source_refs"] == [str(booking)]
    assert len(client.calls) == 1


def test_kb_agent_minimal_extraction_schema_allows_missing_missing_information(tmp_path: Path) -> None:
    booking = tmp_path / "booking-and-schedule.md"
    booking.write_text("# Booking and Schedule\n\nПрыжки обычно по выходным.\n", encoding="utf-8")

    client = SequentialClient(
        [
            {
                "grounding_status": "ready",
                "answer_basis": "Прыжки обычно по выходным.",
                "grounded_facts": ["Прыжки обычно по выходным."],
                "cited_source_refs": [str(booking)],
                "reason": "minimal_schema",
            },
        ]
    )
    service = KBAgentService(client=client)
    answer_context = [
        {
            "source_ref": str(booking),
            "source_type": "wiki_page",
            "page_title": "Booking and Schedule",
            "linked_pages": [],
            "retrieval_notes": "selected page",
            "text": "Прыжки обычно по выходным.",
        }
    ]

    old_minimal = settings.kb_agent_minimal_extraction_schema
    settings.kb_agent_minimal_extraction_schema = True
    try:
        result = service._extract_grounded_facts("Когда обычно проходят прыжки?", answer_context, answer_mode="llm_wiki_selected_pages")
    finally:
        settings.kb_agent_minimal_extraction_schema = old_minimal

    payload = json.loads(client.calls[0]["user_prompt"])
    assert "missing_information" not in payload["required_json_schema"]
    assert result["missing_information"] == []
    assert result["grounded_facts"] == ["Прыжки обычно по выходным."]
