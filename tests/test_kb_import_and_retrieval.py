from pathlib import Path

from app.services.retrieval import RetrievalService
from scripts.build_runtime_profile import build_runtime_profile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "deploy" / "profile-source"


def make_profile(tmp_path: Path) -> Path:
    profile_root = tmp_path / "profile"
    build_runtime_profile(SOURCE, profile_root)
    return profile_root


def test_compiled_kb_exposes_complete_semantic_catalog(tmp_path: Path) -> None:
    profile_root = make_profile(tmp_path)
    retrieval = RetrievalService().retrieve(
        "Произвольная формулировка не должна запускать словарный префильтр.",
        knowledge_backend="filesystem",
        knowledge_root=str(profile_root / "kb"),
    )

    assert retrieval["kb_status"] == "found"
    assert retrieval["kb_mode"] == "llm_wiki_catalog"
    assert retrieval["kb_total_pages"] == 10
    cards = retrieval["kb_snippets"]
    assert len(cards) == 11
    assert cards[0]["source_type"] == "wiki_index"
    page_cards = cards[1:]
    assert len(page_cards) == 10
    assert {card["source_type"] for card in page_cards} == {"wiki_page_card"}
    refs = {card["source_ref"] for card in page_cards}
    assert "compiled/concepts/booking-and-schedule.md" in refs
    assert "compiled/concepts/flight-services.md" in refs


def test_compiled_kb_catalog_descriptions_are_factual_not_reply_scenarios(tmp_path: Path) -> None:
    profile_root = make_profile(tmp_path)
    retrieval = RetrievalService().retrieve(
        "любой вопрос",
        knowledge_backend="filesystem",
        knowledge_root=str(profile_root / "kb"),
    )

    catalog_text = "\n".join(str(card.get("text") or "") for card in retrieval["kb_snippets"])
    assert "Если клиент" not in catalog_text
    assert "customer-facing" not in catalog_text
    assert "готовый ответ" not in catalog_text
