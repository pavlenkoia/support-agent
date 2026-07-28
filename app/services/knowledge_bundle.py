from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

import yaml


PAGE_DIRS = ("concepts", "entities", "comparisons", "queries")
REQUIRED_PAGE_FIELDS = ("title", "created", "updated", "type", "tags", "sources")
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
SUMMARY_RE = re.compile(r"^## Summary\n(.+?)(?:\n## |\Z)", flags=re.MULTILINE | re.DOTALL)


class BundleValidationError(ValueError):
    """Raised when a legacy or compiled LLM Wiki bundle violates its contract."""


def compile_legacy_bundle(legacy_root: Path, target_root: Path) -> dict[str, Any]:
    """Compile a legacy wiki into an isolated, deterministic compatibility bundle.

    The caller must supply a new target directory. This intentionally prevents the
    compiler from mutating an existing mounted/runtime bundle.
    """
    legacy_root = Path(legacy_root)
    target_root = Path(target_root)
    if target_root.exists():
        raise BundleValidationError(f"target bundle already exists: {target_root}")

    source_pages = _discover_pages(legacy_root)
    if not source_pages:
        raise BundleValidationError("legacy bundle has no compiled-page candidates")
    if not (legacy_root / "raw").is_dir():
        raise BundleValidationError("legacy bundle has no raw/ layer")
    if not (legacy_root / "SCHEMA.md").is_file():
        raise BundleValidationError("legacy bundle has no SCHEMA.md")
    if not (legacy_root / "index.md").is_file():
        raise BundleValidationError("legacy bundle has no index.md")

    identities = _build_page_identities(legacy_root, source_pages)
    target_root.mkdir(parents=True)
    shutil.copytree(legacy_root / "raw", target_root / "raw")
    (target_root / "schema").mkdir()
    shutil.copy2(legacy_root / "SCHEMA.md", target_root / "schema" / "SCHEMA.md")
    source_schema_tags = _schema_tags(legacy_root / "SCHEMA.md")
    legacy_undeclared_tags = sorted(
        {tag for page in source_pages for tag in _read_frontmatter(page)[0]["tags"]} - source_schema_tags
    )
    _write_json(
        target_root / "schema" / "compatibility-taxonomy.json",
        {"legacy_undeclared_tags": legacy_undeclared_tags},
    )

    cards: list[dict[str, Any]] = []
    for source_path in source_pages:
        target_relative = identities[source_path]
        target_path = target_root / target_relative
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)
        metadata, body = _read_frontmatter(source_path)
        _validate_page_metadata(metadata, source_path)
        _validate_source_refs(metadata["sources"], source_path)
        cards.append(
            {
                "source_ref": target_relative.as_posix(),
                "title": str(metadata["title"]),
                "description": _extract_summary(body),
                "type": str(metadata["type"]),
                "tags": list(metadata["tags"]),
                "updated": str(metadata["updated"]),
                "provenance": list(metadata["sources"]),
                "linked_source_refs": _resolve_links(body, identities),
            }
        )

    cards.sort(key=lambda card: card["source_ref"])
    (target_root / "index").mkdir()
    catalog = {"catalog_version": 1, "pages": cards}
    _write_json(target_root / "index" / "catalog.json", catalog)
    manifest = {
        "bundle_version": 1,
        "page_count": len(cards),
        "page_identities": [card["source_ref"] for card in cards],
        "raw_file_count": sum(1 for path in (target_root / "raw").rglob("*") if path.is_file()),
        "raw_resources": _build_raw_resources(target_root),
        "catalog_path": "index/catalog.json",
    }
    _write_json(target_root / "index" / "manifest.json", manifest)
    return manifest


def validate_compiled_bundle(bundle_root: Path) -> dict[str, Any]:
    """Validate the isolated compiled-bundle authority chain without runtime I/O."""
    bundle_root = Path(bundle_root)
    manifest = _read_json(bundle_root / "index" / "manifest.json")
    catalog = _read_json(bundle_root / "index" / "catalog.json")
    errors: list[str] = []
    expected_catalog_path = "index/catalog.json"
    if manifest.get("catalog_path") != expected_catalog_path:
        errors.append("manifest catalog_path does not match expected catalog")
    actual_raw_file_count = sum(1 for path in (bundle_root / "raw").rglob("*") if path.is_file())
    if manifest.get("raw_file_count") != actual_raw_file_count:
        errors.append("manifest raw_file_count does not match raw layer")
    cards = catalog.get("pages")
    if not isinstance(cards, list):
        return {"valid": False, "errors": ["catalog pages must be a list"]}

    pages = _discover_pages(bundle_root / "compiled")
    expected_refs = {path.relative_to(bundle_root).as_posix() for path in pages}
    catalog_refs = [str(card.get("source_ref") or "") for card in cards if isinstance(card, dict)]
    if len(catalog_refs) != len(set(catalog_refs)):
        errors.append("catalog contains duplicate source_ref")
    if set(catalog_refs) != expected_refs:
        errors.append("catalog source_ref set does not match compiled pages")
    if manifest.get("page_count") != len(pages):
        errors.append("manifest page_count does not match compiled pages")
    if manifest.get("page_identities") != sorted(expected_refs):
        errors.append("manifest page_identities do not match compiled pages")

    allowed_tags = _schema_tags(bundle_root / "schema" / "SCHEMA.md") | _compatibility_taxonomy_tags(bundle_root / "schema")
    _validate_raw_resources(bundle_root, manifest, errors)
    for page in pages:
        relative_ref = page.relative_to(bundle_root).as_posix()
        try:
            metadata, body = _read_frontmatter(page)
            _validate_page_metadata(metadata, page)
            _validate_source_refs(metadata["sources"], page)
            if allowed_tags and any(tag not in allowed_tags for tag in metadata["tags"]):
                errors.append(f"page has undeclared tag: {relative_ref}")
            for source in metadata["sources"]:
                source_path = bundle_root / str(source)
                if not source_path.is_file():
                    errors.append(f"page source does not resolve: {relative_ref} -> {source}")
            expected_links = _resolve_links(body, {candidate: candidate.relative_to(bundle_root) for candidate in pages})
            card = next((item for item in cards if item.get("source_ref") == relative_ref), None)
            if not isinstance(card, dict) or card.get("linked_source_refs") != expected_links:
                errors.append(f"catalog links do not match page: {relative_ref}")
        except BundleValidationError as exc:
            errors.append(str(exc))

    return {"valid": not errors, "errors": errors}


def _discover_pages(root: Path) -> list[Path]:
    pages: list[Path] = []
    for directory in PAGE_DIRS:
        path = root / directory
        if path.is_dir():
            pages.extend(sorted(path.rglob("*.md")))
    return pages


def _build_page_identities(legacy_root: Path, pages: list[Path]) -> dict[Path, Path]:
    identities: dict[Path, Path] = {}
    seen_stems: set[str] = set()
    for page in pages:
        relative = page.relative_to(legacy_root)
        target = Path("compiled") / relative
        if page.stem in seen_stems:
            raise BundleValidationError(f"ambiguous page slug: {page.stem}")
        seen_stems.add(page.stem)
        identities[page] = target
    return identities


def _read_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise BundleValidationError(f"page has no frontmatter: {path}")
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        raise BundleValidationError(f"page frontmatter is not closed: {path}")
    metadata = yaml.safe_load(parts[0][4:])
    if not isinstance(metadata, dict):
        raise BundleValidationError(f"page frontmatter is not a mapping: {path}")
    return metadata, parts[1]


def _validate_page_metadata(metadata: dict[str, Any], path: Path) -> None:
    missing = [field for field in REQUIRED_PAGE_FIELDS if not metadata.get(field)]
    if missing:
        raise BundleValidationError(f"page missing required metadata ({', '.join(missing)}): {path}")
    if not isinstance(metadata["tags"], list) or not isinstance(metadata["sources"], list):
        raise BundleValidationError(f"page tags and sources must be lists: {path}")


def _validate_source_refs(sources: list[Any], path: Path) -> None:
    for source in sources:
        source_ref = PurePosixPath(str(source))
        if source_ref.is_absolute() or ".." in source_ref.parts or not source_ref.parts or source_ref.parts[0] != "raw":
            raise BundleValidationError(f"page provenance must resolve inside raw/: {path}")


def _resolve_links(body: str, identities: dict[Path, Path]) -> list[str]:
    by_slug = {path.stem: target.as_posix() for path, target in identities.items()}
    linked_refs: list[str] = []
    for raw_link in WIKILINK_RE.findall(body):
        slug = raw_link.strip()
        if slug not in by_slug:
            raise BundleValidationError(f"unresolved wikilink: {slug}")
        resolved = by_slug[slug]
        if resolved not in linked_refs:
            linked_refs.append(resolved)
    return linked_refs


def _extract_summary(body: str) -> str:
    match = SUMMARY_RE.search(body)
    if match:
        return match.group(1).strip()
    lines = [line.strip() for line in body.splitlines() if line.strip() and not line.startswith("#")]
    return " ".join(lines[:3])


def _schema_tags(schema_path: Path) -> set[str]:
    tags: set[str] = set()
    for line in schema_path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*-\s+([a-zA-Z0-9_-]+)\s*$", line)
        if match:
            tags.add(match.group(1))
    return tags


def _build_raw_resources(bundle_root: Path) -> list[dict[str, str]]:
    resources: list[dict[str, str]] = []
    for path in sorted((bundle_root / "raw").rglob("*.md")):
        metadata, body = _read_frontmatter(path)
        resources.append(
            {
                "source_ref": path.relative_to(bundle_root).as_posix(),
                "declared_source_sha256": str(metadata.get("sha256") or ""),
                "content_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            }
        )
    return resources


def _compatibility_taxonomy_tags(schema_dir: Path) -> set[str]:
    path = schema_dir / "compatibility-taxonomy.json"
    if not path.is_file():
        return set()
    payload = _read_json(path)
    tags = payload.get("legacy_undeclared_tags", [])
    return {str(tag) for tag in tags if str(tag)} if isinstance(tags, list) else set()


def _validate_raw_resources(bundle_root: Path, manifest: dict[str, Any], errors: list[str]) -> None:
    listed = manifest.get("raw_resources")
    if not isinstance(listed, list):
        errors.append("manifest raw_resources must be a list")
        return
    by_ref = {str(item.get("source_ref") or ""): item for item in listed if isinstance(item, dict)}
    expected_refs = {path.relative_to(bundle_root).as_posix() for path in (bundle_root / "raw").rglob("*.md")}
    if set(by_ref) != expected_refs:
        errors.append("manifest raw_resources do not match raw layer")
    for ref in sorted(expected_refs):
        item = by_ref.get(ref)
        if not item:
            continue
        metadata, body = _read_frontmatter(bundle_root / ref)
        if item.get("declared_source_sha256") != str(metadata.get("sha256") or ""):
            errors.append(f"raw declared source sha256 changed: {ref}")
        if item.get("content_sha256") != hashlib.sha256(body.encode("utf-8")).hexdigest():
            errors.append(f"raw content sha256 mismatch: {ref}")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise BundleValidationError(f"required bundle artifact missing: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BundleValidationError(f"bundle artifact is not an object: {path}")
    return value
