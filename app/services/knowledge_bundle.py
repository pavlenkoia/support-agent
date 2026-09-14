from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

PAGE_DIRS = ("concepts", "entities", "comparisons", "queries")


class BundleValidationError(ValueError):
    """Raised when authored runtime facts violate the runtime contract."""


def build_runtime_knowledge_artifact(source_root: Path, output_path: Path, max_chars: int = 50_000) -> dict[str, Any]:
    """Compile only explicit runtime_facts from the authored Wiki snapshot."""
    source_root = Path(source_root)
    facts: list[dict[str, Any]] = []
    for page in _discover_pages(source_root):
        metadata = _read_frontmatter(page)
        page_facts = metadata.get("runtime_facts", [])
        if not isinstance(page_facts, list):
            raise BundleValidationError(f"runtime_facts must be a list: {page}")
        facts.extend(page_facts)
    artifact = {"schema_version": 1, "facts": facts}
    validate_runtime_knowledge_artifact(artifact, max_chars=max_chars)
    Path(output_path).write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return artifact


def validate_runtime_knowledge_artifact(artifact: dict[str, Any], max_chars: int = 50_000) -> None:
    """Reject a runtime KB that leaks Wiki/editor material or loses fact integrity."""
    if artifact.get("schema_version") != 1 or set(artifact) != {"schema_version", "facts"} or not isinstance(artifact.get("facts"), list):
        raise BundleValidationError("invalid runtime knowledge artifact")
    facts = artifact["facts"]
    fact_ids: set[str] = set()
    fact_texts: set[str] = set()
    char_count = 0
    for fact in facts:
        if not isinstance(fact, dict) or set(fact) != {"id", "text", "conditions", "source_refs"}:
            raise BundleValidationError("runtime knowledge artifact has invalid fact shape")
        fact_id = fact["id"]
        text = fact["text"]
        conditions = fact["conditions"]
        source_refs = fact["source_refs"]
        if not isinstance(fact_id, str) or not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)+", fact_id) or not isinstance(text, str) or not text:
            raise BundleValidationError("runtime knowledge artifact has invalid fact identity")
        if not isinstance(conditions, list) or not all(isinstance(item, str) for item in conditions):
            raise BundleValidationError("runtime knowledge artifact has invalid conditions")
        if not isinstance(source_refs, list) or not source_refs or not all(isinstance(item, str) and re.fullmatch(r"[a-z-]+:\d+", item) for item in source_refs):
            raise BundleValidationError("runtime knowledge artifact has invalid provenance")
        if fact_id in fact_ids or text in fact_texts:
            raise BundleValidationError("runtime knowledge artifact has duplicate facts")
        if "#" in text or text.casefold().startswith(("если клиент", "клиент может спросить", "готовый ответ", "пример ответа")):
            raise BundleValidationError("runtime knowledge artifact contains Wiki/editor material")
        fact_ids.add(fact_id)
        fact_texts.add(text)
        char_count += len(text)
    if not facts or char_count > max_chars:
        raise BundleValidationError("runtime knowledge artifact has invalid size")


def _discover_pages(root: Path) -> list[Path]:
    pages: list[Path] = []
    for directory in PAGE_DIRS:
        page_root = root / directory
        if page_root.is_dir():
            pages.extend(sorted(page_root.rglob("*.md")))
    return pages


def _read_frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise BundleValidationError(f"page has no frontmatter: {path}")
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        raise BundleValidationError(f"page frontmatter is not closed: {path}")
    metadata = yaml.safe_load(parts[0][4:])
    if not isinstance(metadata, dict):
        raise BundleValidationError(f"page frontmatter is not a mapping: {path}")
    return metadata
