from pathlib import Path

from app.services.retrieval import RetrievalService
from scripts.import_kb import compile_profile


RAW_FILES = {
    "certificates-source.md": "# Certificates\n",
    "general-info-source.md": "# General info\n",
    "faq-main-source.md": "# FAQ main\n",
    "faq-short-source.md": "# FAQ short\n",
}


def make_profile(tmp_path: Path) -> Path:
    profile_root = tmp_path / "profile"
    raw_dir = profile_root / "kb" / "raw" / "documents"
    raw_dir.mkdir(parents=True)
    for name, content in RAW_FILES.items():
        (raw_dir / name).write_text(content, encoding="utf-8")
    compile_profile(profile_root)
    return profile_root


def test_compiled_kb_supports_an2_ticket_purchase_question(tmp_path: Path) -> None:
    profile_root = make_profile(tmp_path)
    query = "Здравствуйте. Хотим приобрести билет в кабину Ан-2, и в салон. Скажите лучше заранее как то приобрести или по месту 27 числа?"

    retrieval = RetrievalService().retrieve(
        query,
        knowledge_backend="filesystem",
        knowledge_root=str(profile_root / "kb"),
    )

    assert retrieval["kb_status"] == "found"
    assert retrieval["kb_mode"] == "llm_wiki_catalog"
    assert retrieval["kb_total_pages"] == 8
    snippets = retrieval["kb_snippets"]
    assert snippets
    assert snippets[0]["source_ref"].endswith("kb/index.md")
    assert snippets[0]["source_type"] == "wiki_index"

    booking = next(snippet for snippet in snippets if snippet["source_ref"].endswith("concepts/booking-and-schedule.md"))
    flights = next(snippet for snippet in snippets if snippet["source_ref"].endswith("concepts/flight-services.md"))

    assert booking["source_type"] == "wiki_page_card"
    assert booking["page_title"] == "Booking and Schedule"
    assert "согласовывать заранее" in booking["text"]
    assert "[[flight-services]]" in booking["text"]

    assert flights["source_type"] == "wiki_page_card"
    assert flights["page_title"] == "Flight Services"
    assert "АН-2" in flights["text"]
    assert "[[booking-and-schedule]]" in flights["text"]
