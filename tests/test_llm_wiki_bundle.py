from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.core.config import settings
from app.integrations.llm.base import BaseLLMClient
from app.services.kb_agent import KBAgentService
from app.services.knowledge_bundle import (
    BundleValidationError,
    compile_legacy_bundle,
    validate_compiled_bundle,
)
from app.services.retrieval import RetrievalService


class FailAfterFirstResponseClient(BaseLLMClient):
    def __init__(self, first_response: dict) -> None:
        self.first_response = first_response
        self.calls = 0

    def generate(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.0, response_format=None) -> str:
        _ = (system_prompt, user_prompt, temperature, response_format)
        self.calls += 1
        if self.calls == 1:
            return json.dumps(self.first_response, ensure_ascii=False)
        raise RuntimeError("forced provider failure")


class FailingClient(BaseLLMClient):
    def generate(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.0, response_format=None) -> str:
        _ = (system_prompt, user_prompt, temperature, response_format)
        raise RuntimeError("forced provider failure")


class ScriptedClient(BaseLLMClient):
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def generate(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.0, response_format=None) -> str:
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt, "temperature": temperature})
        return json.dumps(self.responses.pop(0), ensure_ascii=False)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _legacy_bundle(tmp_path: Path) -> Path:
    root = tmp_path / "legacy"
    raw_body = "# Source\n\nOriginal policy source.\n"
    raw_hash = hashlib.sha256(raw_body.encode("utf-8")).hexdigest()
    _write(
        root / "raw" / "policy.md",
        "---\n"
        "source_url: https://example.test/policy\n"
        "ingested: 2026-07-28\n"
        f"sha256: {raw_hash}\n"
        "---\n"
        f"{raw_body}",
    )
    _write(root / "SCHEMA.md", "# Schema\n\n- approved\n")
    _write(root / "index.md", "# Legacy index\n\n[[booking]]\n[[restrictions]]\n")
    _write(
        root / "concepts" / "booking.md",
        "---\n"
        "title: Booking\n"
        "created: 2026-07-01\n"
        "updated: 2026-07-28\n"
        "type: concept\n"
        "tags: [approved]\n"
        "sources: [raw/policy.md]\n"
        "confidence: high\n"
        "contested: false\n"
        "contradictions: []\n"
        "---\n"
        "# Booking\n\n## Summary\nBooking rules.\n\nSee [[restrictions]].\n",
    )
    _write(
        root / "concepts" / "restrictions.md",
        "---\n"
        "title: Restrictions\n"
        "created: 2026-07-01\n"
        "updated: 2026-07-28\n"
        "type: concept\n"
        "tags: [approved]\n"
        "sources: [raw/policy.md]\n"
        "confidence: high\n"
        "contested: false\n"
        "contradictions: []\n"
        "---\n"
        "# Restrictions\n\n## Summary\nWeight rules.\n",
    )
    return root


def test_compiler_creates_complete_stable_catalog_from_legacy_bundle(tmp_path: Path) -> None:
    legacy_root = _legacy_bundle(tmp_path)
    compiled_root = tmp_path / "compiled"

    manifest = compile_legacy_bundle(legacy_root, compiled_root)
    report = validate_compiled_bundle(compiled_root)

    assert manifest["bundle_version"] == 1
    assert manifest["page_count"] == 2
    assert manifest["raw_file_count"] == 1
    assert manifest["catalog_path"] == "index/catalog.json"
    assert manifest["page_identities"] == [
        "compiled/concepts/booking.md",
        "compiled/concepts/restrictions.md",
    ]
    assert report["valid"] is True

    catalog = json.loads((compiled_root / "index" / "catalog.json").read_text(encoding="utf-8"))
    refs = [card["source_ref"] for card in catalog["pages"]]
    assert refs == [
        "compiled/concepts/booking.md",
        "compiled/concepts/restrictions.md",
    ]
    assert catalog["pages"][0]["linked_source_refs"] == ["compiled/concepts/restrictions.md"]
    assert all(not Path(ref).is_absolute() for ref in refs)
    assert (compiled_root / "raw" / "policy.md").read_bytes() == (legacy_root / "raw" / "policy.md").read_bytes()


def test_compiler_records_legacy_external_hash_and_taxonomy_extension_without_mutating_raw(tmp_path: Path) -> None:
    legacy_root = _legacy_bundle(tmp_path)
    raw_path = legacy_root / "raw" / "policy.md"
    raw_text = raw_path.read_text(encoding="utf-8")
    raw_path.write_text(raw_text.replace("sha256: ", "sha256: legacy-external-", 1), encoding="utf-8")
    booking_path = legacy_root / "concepts" / "booking.md"
    booking_path.write_text(
        booking_path.read_text(encoding="utf-8").replace("tags: [approved]", "tags: [approved, legacy-extra]", 1),
        encoding="utf-8",
    )
    original_raw_bytes = raw_path.read_bytes()
    compiled_root = tmp_path / "compiled"

    manifest = compile_legacy_bundle(legacy_root, compiled_root)
    report = validate_compiled_bundle(compiled_root)

    assert report["valid"] is True
    assert (compiled_root / "raw" / "policy.md").read_bytes() == original_raw_bytes
    resource = manifest["raw_resources"][0]
    assert resource["source_ref"] == "raw/policy.md"
    assert resource["declared_source_sha256"] == "legacy-external-" + hashlib.sha256(b"# Source\n\nOriginal policy source.\n").hexdigest()
    assert resource["content_sha256"] == hashlib.sha256(b"# Source\n\nOriginal policy source.\n").hexdigest()
    taxonomy = json.loads((compiled_root / "schema" / "compatibility-taxonomy.json").read_text(encoding="utf-8"))
    assert taxonomy["legacy_undeclared_tags"] == ["legacy-extra"]


def test_retrieval_uses_complete_catalog_for_compiled_bundle_regardless_of_query(tmp_path: Path) -> None:
    legacy_root = _legacy_bundle(tmp_path)
    compiled_root = tmp_path / "compiled"
    compile_legacy_bundle(legacy_root, compiled_root)

    retrieval = RetrievalService().retrieve(
        "unrelated wording must not prefilter compiled wiki cards",
        knowledge_backend="filesystem",
        knowledge_root=str(compiled_root),
    )

    assert retrieval["kb_status"] == "found"
    assert retrieval["kb_architecture"] == "llm_wiki"
    assert retrieval["kb_mode"] == "llm_wiki_catalog"
    assert retrieval["navigation_mode"] == "llm"
    cards = [item for item in retrieval["kb_snippets"] if item["source_type"] == "wiki_page_card"]
    assert [item["source_ref"] for item in cards] == [
        "compiled/concepts/booking.md",
        "compiled/concepts/restrictions.md",
    ]
    assert cards[0]["linked_pages"] == ["compiled/concepts/restrictions.md"]
    assert cards[0]["source_path"].endswith("compiled/concepts/booking.md")


def test_compiled_bundle_runs_llm_navigation_then_coverage_review_before_extraction(tmp_path: Path) -> None:
    legacy_root = _legacy_bundle(tmp_path)
    compiled_root = tmp_path / "compiled"
    compile_legacy_bundle(legacy_root, compiled_root)
    retrieval = RetrievalService().retrieve("What are the booking restrictions?", "filesystem", str(compiled_root))
    client = ScriptedClient(
        [
            {
                "user_intent": "booking restrictions",
                "information_needs": ["booking", "restrictions"],
                "selected_source_refs": ["compiled/concepts/booking.md"],
                "reason": "booking page is the starting point",
            },
            {
                "coverage_status": "need_more_pages",
                "missing_facts": ["restrictions"],
                "additional_source_refs": ["compiled/concepts/restrictions.md"],
                "reason": "authored booking relation supplies restrictions",
            },
            {
                "grounding_status": "ready",
                "answer_basis": "Booking rules and weight rules are confirmed.",
                "grounded_facts": ["Booking rules.", "Weight rules."],
                "missing_information": [],
                "cited_source_refs": [
                    "compiled/concepts/booking.md",
                    "compiled/concepts/restrictions.md",
                ],
                "reason": "grounded_from_selected_pages",
            },
        ]
    )

    result = KBAgentService(client=client).read("What are the booking restrictions?", retrieval["kb_snippets"])

    assert result["grounding_status"] == "ready"
    assert result["source_refs"] == [
        "compiled/concepts/booking.md",
        "compiled/concepts/restrictions.md",
    ]
    assert [page["source_ref"] for page in result["answer_context"]] == result["source_refs"]
    assert result["trace"]["kb_architecture"] == "llm_wiki"
    assert result["trace"]["navigation_mode"] == "llm"
    assert result["trace"]["coverage_review_mode"] == "llm"
    assert result["trace"]["review"]["coverage_status"] == "need_more_pages"
    assert len(client.calls) == 3


def test_compiled_bundle_ignores_legacy_deterministic_and_coverage_skip_flags(tmp_path: Path) -> None:
    legacy_root = _legacy_bundle(tmp_path)
    compiled_root = tmp_path / "compiled"
    compile_legacy_bundle(legacy_root, compiled_root)
    retrieval = RetrievalService().retrieve("What are the booking restrictions?", "filesystem", str(compiled_root))
    client = ScriptedClient(
        [
            {
                "user_intent": "booking restrictions",
                "information_needs": ["booking"],
                "selected_source_refs": ["compiled/concepts/booking.md"],
                "reason": "LLM navigation",
            },
            {
                "coverage_status": "enough",
                "missing_facts": [],
                "additional_source_refs": [],
                "reason": "LLM coverage review",
            },
            {
                "grounding_status": "ready",
                "answer_basis": "Booking rules are confirmed.",
                "grounded_facts": ["Booking rules."],
                "missing_information": [],
                "cited_source_refs": ["compiled/concepts/booking.md"],
                "reason": "grounded_from_selected_page",
            },
        ]
    )
    old_navigation = settings.kb_agent_deterministic_navigation
    old_coverage = settings.kb_agent_skip_coverage_review
    settings.kb_agent_deterministic_navigation = True
    settings.kb_agent_skip_coverage_review = True
    try:
        result = KBAgentService(client=client).read("What are the booking restrictions?", retrieval["kb_snippets"])
    finally:
        settings.kb_agent_deterministic_navigation = old_navigation
        settings.kb_agent_skip_coverage_review = old_coverage

    assert result["grounding_status"] == "ready"
    assert result["trace"]["navigation"]["reason"] == "LLM navigation"
    assert result["trace"]["review"]["reason"] == "LLM coverage review"
    assert len(client.calls) == 3


def test_compiled_bundle_navigation_failure_is_retry_pending_without_lexical_selection(tmp_path: Path) -> None:
    legacy_root = _legacy_bundle(tmp_path)
    compiled_root = tmp_path / "compiled"
    compile_legacy_bundle(legacy_root, compiled_root)
    retrieval = RetrievalService().retrieve("What are the booking restrictions?", "filesystem", str(compiled_root))

    result = KBAgentService(client=FailingClient()).read("What are the booking restrictions?", retrieval["kb_snippets"])

    assert result["grounding_status"] == "retry_pending"
    assert result["source_refs"] == []
    assert result["answer_context"] == []
    assert result["trace"]["selected_source_refs"] == []
    assert result["trace"]["navigation"]["reason"] == "navigation_error:RuntimeError"


def test_compiled_bundle_coverage_failure_is_retry_pending_without_extraction(tmp_path: Path) -> None:
    legacy_root = _legacy_bundle(tmp_path)
    compiled_root = tmp_path / "compiled"
    compile_legacy_bundle(legacy_root, compiled_root)
    retrieval = RetrievalService().retrieve("What are the booking restrictions?", "filesystem", str(compiled_root))
    client = FailAfterFirstResponseClient(
        {
            "user_intent": "booking restrictions",
            "information_needs": ["booking"],
            "selected_source_refs": ["compiled/concepts/booking.md"],
            "reason": "booking page is the starting point",
        }
    )

    result = KBAgentService(client=client).read("What are the booking restrictions?", retrieval["kb_snippets"])

    assert result["grounding_status"] == "retry_pending"
    assert result["answer_context"] == []
    assert result["source_refs"] == []
    assert result["trace"]["review"]["reason"] == "coverage_review_error:RuntimeError"
    assert client.calls == 2


def test_compiler_rejects_page_provenance_outside_raw_layer(tmp_path: Path) -> None:
    legacy_root = _legacy_bundle(tmp_path)
    booking = legacy_root / "concepts" / "booking.md"
    booking.write_text(
        booking.read_text(encoding="utf-8").replace("sources: [raw/policy.md]", "sources: [/etc/passwd]"),
        encoding="utf-8",
    )

    try:
        compile_legacy_bundle(legacy_root, tmp_path / "compiled")
    except BundleValidationError as exc:
        assert "raw/" in str(exc)
    else:
        raise AssertionError("compiler accepted provenance outside raw/")


def test_validator_rejects_manifest_that_disagrees_with_bundle_artifacts(tmp_path: Path) -> None:
    legacy_root = _legacy_bundle(tmp_path)
    compiled_root = tmp_path / "compiled"
    compile_legacy_bundle(legacy_root, compiled_root)
    manifest_path = compiled_root / "index" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["catalog_path"] = "index/other.json"
    manifest["raw_file_count"] = 99
    manifest["page_identities"] = []
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = validate_compiled_bundle(compiled_root)

    assert report["valid"] is False
    assert "manifest catalog_path does not match expected catalog" in report["errors"]
    assert "manifest raw_file_count does not match raw layer" in report["errors"]
    assert "manifest page_identities do not match compiled pages" in report["errors"]
