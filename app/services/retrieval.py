from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


TOKEN_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9]{3,}")
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
PAGE_DIRS = ("concepts", "entities", "comparisons", "queries")
MAX_SNIPPET_CHARS = 900
SMALL_WIKI_MAX_TOTAL_CHARS = 25_000
SMALL_WIKI_MAX_PAGES = 16
INDEX_MAX_CHARS = 4_000
SUMMARY_MAX_CHARS = 700


@dataclass
class PageHit:
    path: Path
    score: int
    title: str
    snippet: str


class RetrievalService:
    def retrieve(
        self,
        query: str,
        knowledge_backend: str,
        knowledge_root: str,
        *,
        current_query: str | None = None,
    ) -> dict:
        if knowledge_backend != "filesystem":
            return {"kb_status": "not_found", "kb_snippets": []}

        wiki_root = Path(knowledge_root)
        compiled_catalog_path = wiki_root / "index" / "catalog.json"
        if compiled_catalog_path.is_file():
            return self._build_compiled_llm_wiki_catalog(wiki_root, compiled_catalog_path)
        index_path = wiki_root / "index.md"
        if not index_path.exists():
            return {"kb_status": "not_found", "kb_snippets": []}

        pages = self._discover_pages(wiki_root)
        if not pages:
            return {"kb_status": "not_found", "kb_snippets": []}

        query_terms = self._tokenize(query)
        primary_query = str(current_query or query).strip()
        primary_terms = self._tokenize(primary_query)
        if not primary_terms:
            return {"kb_status": "not_found", "kb_snippets": []}

        page_texts = {page: page.read_text(encoding="utf-8") for page in pages}
        total_chars = sum(len(text) for text in page_texts.values())
        retrieval_contract = {
            "current_query": primary_query,
            "context_query": str(query or "").strip(),
            "linked_expansions": [],
        }

        if self._should_use_llm_wiki_catalog(total_chars=total_chars, page_count=len(pages)):
            result = self._build_llm_wiki_catalog(index_path=index_path, page_texts=page_texts, total_chars=total_chars)
            result["retrieval_contract"] = retrieval_contract
            return result

        hits: list[PageHit] = []
        context_terms = query_terms - primary_terms
        for page, text in page_texts.items():
            primary_score = self._score_page(text, primary_terms)
            context_score = self._score_page(text, context_terms)
            score = primary_score * 4 + context_score
            if score <= 0:
                continue
            title = self._extract_title(page, text)
            snippet = self._extract_snippet(text, primary_terms or query_terms)
            hits.append(PageHit(path=page, score=score, title=title, snippet=snippet))

        if not hits:
            return {"kb_status": "not_found", "kb_snippets": []}

        hits.sort(key=lambda item: (-item.score, str(item.path)))
        selected_hits = hits[:5]
        selected_hits, linked_expansions = self._expand_linked_hits(selected_hits, page_texts, primary_terms)
        retrieval_contract["linked_expansions"] = linked_expansions
        snippets = [
            {
                "text": hit.snippet,
                "source_type": "wiki_page",
                "source_ref": str(hit.path),
                "retrieval_notes": f"llm-wiki compiled page match (score={hit.score}, title={hit.title})",
            }
            for hit in selected_hits
        ]
        return {
            "kb_status": "found",
            "kb_snippets": snippets,
            "kb_mode": "lexical_page_match",
            "retrieval_contract": retrieval_contract,
        }

    def expand_for_grounding(
        self,
        retrieval: dict,
        *,
        current_query: str,
        knowledge_backend: str,
        knowledge_root: str,
        limit: int = 2,
    ) -> dict:
        """Load a bounded set of linked pages after an honest missing-fact result."""
        if knowledge_backend != "filesystem" or retrieval.get("kb_status") != "found":
            return retrieval
        existing_hits = list(retrieval.get("kb_snippets") or [])
        existing_refs = {str(item.get("source_ref")) for item in existing_hits if item.get("source_ref")}
        wiki_root = Path(knowledge_root)
        pages = self._discover_pages(wiki_root)
        page_texts = {page: page.read_text(encoding="utf-8") for page in pages}
        by_path = {str(page): page for page in pages}
        by_slug = {page.stem.lower(): page for page in pages}
        query_terms = self._tokenize(current_query)
        additions: list[dict] = []
        expansions: list[dict[str, str]] = []
        candidates: dict[Path, list[str]] = {}
        for hit in existing_hits:
            source_ref = str(hit.get("source_ref") or "")
            source = by_path.get(source_ref)
            if source is None:
                continue
            for link in self._extract_wikilinks(page_texts[source]):
                target = by_slug.get(link.strip().lower())
                if target is None or str(target) in existing_refs:
                    continue
                candidates.setdefault(target, []).append(source_ref)
        ranked_candidates = sorted(
            candidates,
            key=lambda target: (-self._score_page(page_texts[target], query_terms), str(target)),
        )
        for target in ranked_candidates[:limit]:
            target_text = page_texts[target]
            additions.append(
                {
                    "text": self._extract_snippet(target_text, query_terms),
                    "source_type": "wiki_page",
                    "source_ref": str(target),
                    "retrieval_notes": "linked-page grounding expansion after missing critical fact",
                }
            )
            expansions.append(
                {"from_source_ref": candidates[target][0], "source_ref": str(target)}
            )
        if not additions:
            return retrieval
        contract = dict(retrieval.get("retrieval_contract") or {})
        contract["grounding_expansion"] = expansions
        return {**retrieval, "kb_snippets": [*existing_hits, *additions], "retrieval_contract": contract}

    def _expand_linked_hits(
        self,
        selected_hits: list[PageHit],
        page_texts: dict[Path, str],
        query_terms: set[str],
        *,
        limit: int = 2,
    ) -> tuple[list[PageHit], list[dict[str, str]]]:
        """Boundedly follow wiki links from the initially relevant pages.

        Links are authored KB relations, so this is a generic completeness expansion,
        not a query-topic rule.  The original ranked hits stay first; related pages
        are only appended and remain provenance-labelled in the returned context.
        """
        by_slug = {page.stem.lower(): page for page in page_texts}
        selected_paths = {hit.path for hit in selected_hits}
        expansions: list[dict[str, str]] = []
        expanded_hits: list[PageHit] = list(selected_hits)
        for hit in selected_hits:
            for link in self._extract_wikilinks(page_texts[hit.path]):
                target = by_slug.get(link.strip().lower())
                if target is None or target in selected_paths:
                    continue
                text = page_texts[target]
                expanded_hits.append(
                    PageHit(
                        path=target,
                        score=self._score_page(text, query_terms),
                        title=self._extract_title(target, text),
                        snippet=self._extract_snippet(text, query_terms),
                    )
                )
                selected_paths.add(target)
                expansions.append({"from_source_ref": str(hit.path), "source_ref": str(target)})
                if len(expansions) >= limit:
                    return expanded_hits, expansions
        return expanded_hits, expansions

    def _discover_pages(self, wiki_root: Path) -> list[Path]:
        pages: list[Path] = []
        for directory in PAGE_DIRS:
            page_dir = wiki_root / directory
            if not page_dir.exists():
                continue
            pages.extend(sorted(page_dir.rglob("*.md")))
        return pages

    def _should_use_llm_wiki_catalog(self, *, total_chars: int, page_count: int) -> bool:
        return page_count <= SMALL_WIKI_MAX_PAGES and total_chars <= SMALL_WIKI_MAX_TOTAL_CHARS

    def _build_compiled_llm_wiki_catalog(self, wiki_root: Path, catalog_path: Path) -> dict:
        """Expose every validated compiled page to LLM navigation without query scoring."""
        raw_catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        cards = raw_catalog.get("pages") if isinstance(raw_catalog, dict) else None
        if not isinstance(cards, list):
            return {"kb_status": "not_found", "kb_snippets": []}

        snippets: list[dict] = [
            {
                "text": "# Compiled Wiki Catalog\n\nAll compiled page cards follow.",
                "source_type": "wiki_index",
                "source_ref": "index/catalog.json",
                "source_path": str(catalog_path),
                "retrieval_notes": "complete compiled-wiki catalog for LLM navigation",
                "retrieval_mode": "llm_wiki_catalog",
                "kb_architecture": "llm_wiki",
                "navigation_mode": "llm",
                "coverage_review_mode": "llm",
                "extraction_mode": "grounded",
            }
        ]
        for card in cards:
            if not isinstance(card, dict):
                return {"kb_status": "not_found", "kb_snippets": []}
            source_ref = str(card.get("source_ref") or "")
            source_path = wiki_root / source_ref
            if not source_ref or not source_path.is_file():
                return {"kb_status": "not_found", "kb_snippets": []}
            title = str(card.get("title") or Path(source_ref).stem)
            summary = str(card.get("description") or "")
            linked_pages = [str(link) for link in card.get("linked_source_refs", []) if str(link)]
            snippets.append(
                {
                    "text": self._format_page_card(title=title, summary=summary, preview="", linked_pages=linked_pages),
                    "source_type": "wiki_page_card",
                    "source_ref": source_ref,
                    "source_path": str(source_path),
                    "retrieval_notes": "compiled-wiki page card for LLM navigation before selective full-page reading",
                    "retrieval_mode": "llm_wiki_catalog",
                    "kb_architecture": "llm_wiki",
                    "navigation_mode": "llm",
                    "coverage_review_mode": "llm",
                    "extraction_mode": "grounded",
                    "page_title": title,
                    "linked_pages": linked_pages,
                    "page_summary": summary,
                    "page_preview": "",
                }
            )
        return {
            "kb_status": "found",
            "kb_snippets": snippets,
            "kb_mode": "llm_wiki_catalog",
            "kb_architecture": "llm_wiki",
            "navigation_mode": "llm",
            "coverage_review_mode": "llm",
            "extraction_mode": "grounded",
            "kb_total_pages": len(cards),
            "kb_total_chars": sum(len(str(item.get("text") or "")) for item in snippets),
        }

    def _build_llm_wiki_catalog(self, *, index_path: Path, page_texts: dict[Path, str], total_chars: int) -> dict:
        snippets: list[dict] = [
            {
                "text": self._format_index(index_path.read_text(encoding="utf-8")),
                "source_type": "wiki_index",
                "source_ref": str(index_path),
                "retrieval_notes": "llm-wiki catalog index for page navigation across a small compiled wiki",
                "retrieval_mode": "llm_wiki_catalog",
            }
        ]

        for page in sorted(page_texts):
            text = page_texts[page]
            title = self._extract_title(page, text)
            summary = self._extract_summary(self._strip_frontmatter(text))
            preview = self._extract_preview(self._strip_frontmatter(text))
            linked_pages = self._extract_wikilinks(text)
            snippets.append(
                {
                    "text": self._format_page_card(title=title, summary=summary, preview=preview, linked_pages=linked_pages),
                    "source_type": "wiki_page_card",
                    "source_ref": str(page),
                    "retrieval_notes": "llm-wiki page card for LLM page selection before selective full-page reading",
                    "retrieval_mode": "llm_wiki_catalog",
                    "page_title": title,
                    "linked_pages": linked_pages,
                    "page_summary": summary,
                    "page_preview": preview,
                }
            )

        return {
            "kb_status": "found",
            "kb_snippets": snippets,
            "kb_mode": "llm_wiki_catalog",
            "kb_total_pages": len(page_texts),
            "kb_total_chars": total_chars,
        }

    def _format_index(self, text: str) -> str:
        cleaned = self._strip_frontmatter(text).strip()
        return cleaned[:INDEX_MAX_CHARS]

    def _format_page_card(self, *, title: str, summary: str, preview: str, linked_pages: list[str]) -> str:
        lines = [f"# {title}"]
        if summary:
            lines.append("## Summary")
            lines.append(summary[:SUMMARY_MAX_CHARS])
        if preview:
            lines.append("## Key facts")
            lines.append(preview)
        if linked_pages:
            lines.append("## Related pages")
            lines.append(", ".join(f"[[{page}]]" for page in linked_pages))
        return "\n\n".join(lines)

    def _strip_frontmatter(self, text: str) -> str:
        if not text.startswith("---\n"):
            return text
        parts = text.split("\n---\n", 1)
        if len(parts) == 2:
            return parts[1]
        return text

    def _extract_summary(self, body: str) -> str:
        match = re.search(r"^## Summary\n(.+?)(?:\n## |\Z)", body, flags=re.MULTILINE | re.DOTALL)
        if not match:
            return ""
        return match.group(1).strip()

    def _extract_preview(self, body: str) -> str:
        lines: list[str] = []
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("title:"):
                continue
            lines.append(line)
        preview_lines = lines[:6]
        return "\n".join(preview_lines)[:SUMMARY_MAX_CHARS]

    def _extract_wikilinks(self, text: str) -> list[str]:
        links = [match.strip() for match in WIKILINK_RE.findall(text)]
        unique_links: list[str] = []
        for link in links:
            if link not in unique_links:
                unique_links.append(link)
        return unique_links

    def _tokenize(self, text: str) -> set[str]:
        return {token.lower() for token in TOKEN_RE.findall(text.lower())}

    def _score_page(self, text: str, query_terms: set[str]) -> int:
        lowered = text.lower()
        page_terms = self._tokenize(lowered)
        score = 0
        for term in query_terms:
            count = lowered.count(term)
            if not count:
                count = sum(self._terms_share_stem(term, page_term) for page_term in page_terms)
            if count:
                score += min(count, 6)
        if "## summary" in lowered or "## краткий вывод" in lowered:
            score += 1
        return score

    @staticmethod
    def _terms_share_stem(query_term: str, page_term: str) -> bool:
        """Small language-neutral inflection tolerance without a topic vocabulary."""
        if query_term == page_term:
            return True
        prefix_length = 3 if len(query_term) <= 5 else 4
        return len(page_term) >= prefix_length and query_term[:prefix_length] == page_term[:prefix_length]

    def _extract_title(self, page: Path, text: str) -> str:
        title_match = re.search(r"^title:\s*(.+)$", text, flags=re.MULTILINE)
        if title_match:
            return title_match.group(1).strip().strip('"')
        heading_match = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
        if heading_match:
            return heading_match.group(1).strip()
        return page.stem

    def _extract_snippet(self, text: str, query_terms: set[str]) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("---")]
        matched: list[str] = []
        for line in lines:
            line_terms = self._tokenize(line)
            if any(
                term in line.lower() or any(self._terms_share_stem(term, line_term) for line_term in line_terms)
                for term in query_terms
            ):
                matched.append(line)
        if not matched:
            matched = lines[:6]
        snippet = "\n".join(matched[:8])
        return snippet[:MAX_SNIPPET_CHARS]
