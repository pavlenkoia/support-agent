from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


TOKEN_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9]{3,}")
PAGE_DIRS = ("concepts", "entities", "comparisons", "queries")
MAX_SNIPPET_CHARS = 900


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

        hits: list[PageHit] = []
        for page in pages:
            text = page.read_text(encoding="utf-8")
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
        return {"kb_status": "found", "kb_snippets": snippets}

    def _discover_pages(self, wiki_root: Path) -> list[Path]:
        pages: list[Path] = []
        for directory in PAGE_DIRS:
            page_dir = wiki_root / directory
            if not page_dir.exists():
                continue
            pages.extend(sorted(page_dir.rglob("*.md")))
        return pages

    def _tokenize(self, text: str) -> set[str]:
        return {token.lower() for token in TOKEN_RE.findall(text.lower())}

    def _score_page(self, text: str, query_terms: set[str]) -> int:
        lowered = text.lower()
        score = 0
        for term in query_terms:
            count = lowered.count(term)
            if count:
                score += min(count, 6)
        if "## summary" in lowered or "## краткий вывод" in lowered or "## summary" in lowered:
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
