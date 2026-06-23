import json
from pathlib import Path

from app.integrations.llm.base import BaseLLMClient
from app.services.direct_llm import DirectLLMService


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
        response = self.responses.pop(0)
        return json.dumps(response, ensure_ascii=False)


def test_direct_llm_navigates_catalog_reviews_coverage_and_answers_from_selected_pages(tmp_path: Path) -> None:
    booking = tmp_path / "booking-and-schedule.md"
    booking.write_text("# Booking and Schedule\n\nЛучше согласовать заранее.\n", encoding="utf-8")
    flights = tmp_path / "flight-services.md"
    flights.write_text("# Flight Services\n\nЕсть кабина АН-2 и салон.\n", encoding="utf-8")

    client = SequentialClient(
        [
            {
                "user_intent": "понять, как купить полет на АН-2",
                "information_needs": ["способ покупки", "нужно ли заранее"],
                "selected_source_refs": [str(booking), str(flights)],
                "reason": "Нужны запись и услуги АН-2",
            },
            {
                "coverage_status": "enough",
                "missing_facts": [],
                "additional_source_refs": [],
                "reason": "Выбранные страницы покрывают вопрос",
            },
            {
                "direct_status": "ready",
                "decision": "answer",
                "response_text": "Ответ из выбранных страниц wiki.",
                "confidence": 0.92,
                "reason": "wiki_grounded",
            },
        ]
    )
    service = DirectLLMService(client=client)
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
            "page_preview": "Лучше согласовать заранее.",
            "retrieval_mode": "llm_wiki_catalog",
            "text": "# Booking and Schedule\n\n## Summary\nЗапись и расписание полетов.\n\n## Key facts\nЛучше согласовать заранее.",
        },
        {
            "source_ref": str(flights),
            "source_type": "wiki_page_card",
            "page_title": "Flight Services",
            "linked_pages": ["booking-and-schedule"],
            "page_summary": "АН-2, кабина и салон.",
            "page_preview": "Есть кабина АН-2 и салон.",
            "retrieval_mode": "llm_wiki_catalog",
            "text": "# Flight Services\n\n## Summary\nАН-2, кабина и салон.\n\n## Key facts\nЕсть кабина АН-2 и салон.",
        },
    ]

    result = service.answer("Что есть по Ан-2?", kb_hits)

    assert result["decision"] == "answer"
    assert result["used_kb_sources"] == [str(booking), str(flights)]
    assert len(client.calls) == 3

    selection_payload = json.loads(client.calls[0]["user_prompt"])
    assert selection_payload["task"] == "Plan wiki navigation before any full-page reading."
    assert len(selection_payload["wiki_catalog"]) == 3

    review_payload = json.loads(client.calls[1]["user_prompt"])
    assert review_payload["task"].startswith("Check whether the selected wiki pages")
    assert len(review_payload["selected_full_pages"]) == 2
    assert "Лучше согласовать заранее" in review_payload["selected_full_pages"][0]["text"]

    answer_payload = json.loads(client.calls[2]["user_prompt"])
    assert answer_payload["kb_mode"] == "llm_wiki_navigation_selected_pages"
    assert len(answer_payload["kb_snippets"]) == 2
    assert answer_payload["kb_snippets"][0]["source_ref"] == str(booking)
    assert "Лучше согласовать заранее" in answer_payload["kb_snippets"][0]["text"]
    assert answer_payload["kb_snippets"][1]["source_ref"] == str(flights)
    assert "кабина АН-2" in answer_payload["kb_snippets"][1]["text"]


def test_direct_llm_can_request_one_more_page_after_full_page_review(tmp_path: Path) -> None:
    booking = tmp_path / "booking-and-schedule.md"
    booking.write_text("# Booking and Schedule\n\nЛучше согласовать заранее.\n", encoding="utf-8")
    flights = tmp_path / "flight-services.md"
    flights.write_text("# Flight Services\n\nЕсть кабина АН-2 и салон.\n", encoding="utf-8")
    office = tmp_path / "office-chelyabinsk.md"
    office.write_text("# Office Chelyabinsk\n\nОплатить можно в офисе по будням.\n", encoding="utf-8")

    client = SequentialClient(
        [
            {
                "user_intent": "понять, как купить полет на АН-2",
                "information_needs": ["способ покупки", "нужно ли заранее"],
                "selected_source_refs": [str(booking), str(flights)],
                "reason": "Сначала достаточно страниц о записи и полетах",
            },
            {
                "coverage_status": "need_more_pages",
                "missing_facts": ["где оплатить"],
                "additional_source_refs": [str(office)],
                "reason": "Нужен канал оплаты из страницы офиса",
            },
            {
                "direct_status": "ready",
                "decision": "answer",
                "response_text": "Ответ после дочитывания ещё одной страницы.",
                "confidence": 0.95,
                "reason": "wiki_grounded",
            },
        ]
    )
    service = DirectLLMService(client=client)
    kb_hits = [
        {
            "source_ref": str(tmp_path / "index.md"),
            "source_type": "wiki_index",
            "retrieval_mode": "llm_wiki_catalog",
            "text": "# Wiki Index\n[[booking-and-schedule]]\n[[flight-services]]\n[[office-chelyabinsk]]",
        },
        {
            "source_ref": str(booking),
            "source_type": "wiki_page_card",
            "page_title": "Booking and Schedule",
            "linked_pages": ["flight-services", "office-chelyabinsk"],
            "page_summary": "Запись и расписание полетов.",
            "page_preview": "Лучше согласовать заранее.",
            "retrieval_mode": "llm_wiki_catalog",
            "text": "# Booking and Schedule",
        },
        {
            "source_ref": str(flights),
            "source_type": "wiki_page_card",
            "page_title": "Flight Services",
            "linked_pages": ["booking-and-schedule"],
            "page_summary": "АН-2, кабина и салон.",
            "page_preview": "Есть кабина АН-2 и салон.",
            "retrieval_mode": "llm_wiki_catalog",
            "text": "# Flight Services",
        },
        {
            "source_ref": str(office),
            "source_type": "wiki_page_card",
            "page_title": "Office Chelyabinsk",
            "linked_pages": ["booking-and-schedule"],
            "page_summary": "Офис для оплаты и вопросов.",
            "page_preview": "Оплатить можно в офисе по будням.",
            "retrieval_mode": "llm_wiki_catalog",
            "text": "# Office Chelyabinsk",
        },
    ]

    result = service.answer("Как купить полет на АН-2?", kb_hits)

    assert result["decision"] == "answer"
    assert result["used_kb_sources"] == [str(booking), str(flights), str(office)]

    answer_payload = json.loads(client.calls[2]["user_prompt"])
    assert len(answer_payload["kb_snippets"]) == 3
    assert answer_payload["kb_snippets"][2]["source_ref"] == str(office)
    assert "Оплатить можно в офисе по будням" in answer_payload["kb_snippets"][2]["text"]


def test_direct_llm_keeps_legacy_five_hit_cap_for_non_catalog_snippets() -> None:
    client = SequentialClient(
        [
            {
                "direct_status": "ready",
                "decision": "answer",
                "response_text": "Ответ из snippets.",
                "confidence": 0.81,
                "reason": "snippet_grounded",
            }
        ]
    )
    service = DirectLLMService(client=client)
    kb_hits = [
        {
            "source_ref": f"kb://{idx}",
            "source_type": "wiki_page",
            "text": f"Snippet {idx}",
        }
        for idx in range(7)
    ]

    result = service.answer("Что есть по вопросу?", kb_hits)

    assert result["decision"] == "answer"
    payload = json.loads(client.calls[0]["user_prompt"])
    assert payload["kb_mode"] == "retrieved_snippets"
    assert len(payload["kb_snippets"]) == 5
