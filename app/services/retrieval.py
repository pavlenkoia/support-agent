from __future__ import annotations

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
    def retrieve(self, query: str, knowledge_backend: str, knowledge_root: str) -> dict:
        if knowledge_backend != "filesystem":
            return {"kb_status": "not_found", "kb_snippets": []}

        wiki_root = Path(knowledge_root)
        index_path = wiki_root / "index.md"
        if not index_path.exists():
            return {"kb_status": "not_found", "kb_snippets": []}

        pages = self._discover_pages(wiki_root)
        if not pages:
            return {"kb_status": "not_found", "kb_snippets": []}

        query_terms = self._tokenize(query)
        if not query_terms:
            return {"kb_status": "not_found", "kb_snippets": []}

        page_texts = {page: page.read_text(encoding="utf-8") for page in pages}
        total_chars = sum(len(text) for text in page_texts.values())

        if self._should_use_llm_wiki_catalog(total_chars=total_chars, page_count=len(pages)):
            return self._build_llm_wiki_catalog(index_path=index_path, page_texts=page_texts, total_chars=total_chars)

        hits: list[PageHit] = []
        for page, text in page_texts.items():
            score = self._score_page(text, query_terms)
            if score <= 0:
                continue
            title = self._extract_title(page, text)
            snippet = self._extract_snippet(text, query_terms)
            hits.append(PageHit(path=page, score=score, title=title, snippet=snippet))

        if not hits:
            return {"kb_status": "not_found", "kb_snippets": []}

        hits.sort(key=lambda item: (-item.score, str(item.path)))
        snippets = [
            {
                "text": hit.snippet,
                "source_type": "wiki_page",
                "source_ref": str(hit.path),
                "retrieval_notes": f"llm-wiki compiled page match (score={hit.score}, title={hit.title})",
            }
            for hit in hits[:5]
        ]
        return {"kb_status": "found", "kb_snippets": snippets, "kb_mode": "lexical_page_match"}

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
        score = 0
        for term in query_terms:
            count = lowered.count(term)
            if count:
                score += min(count, 6)
        if "## summary" in lowered or "## краткий вывод" in lowered:
            score += 1
        return score

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
            lowered = line.lower()
            if any(term in lowered for term in query_terms):
                matched.append(line)
        if not matched:
            matched = lines[:6]
        snippet = "\n".join(matched[:8])
        return snippet[:MAX_SNIPPET_CHARS]
