from __future__ import annotations

from typing import Any, Protocol


class WikiLookup(Protocol):
    def lookup(self, *, text: str, context: dict) -> dict[str, Any]: ...


class UnifiedTurnModel(Protocol):
    def begin_turn(self, *, text: str, context: dict) -> dict[str, Any]: ...


class UnifiedTurnService:
    """One customer-facing model turn may return text or request the Wiki tool."""

    def __init__(self, *, model: UnifiedTurnModel, wiki_lookup: WikiLookup) -> None:
        self.model = model
        self.wiki_lookup = wiki_lookup

    def run(self, *, text: str, context: dict) -> dict[str, Any]:
        # Wiki is mandatory for every customer turn.  There is no semantic
        # application router: the runtime always executes the same bounded
        # factual tool before the sole customer-facing finalization call.
        wiki_result = self.wiki_lookup.lookup(text=text, context=context)
        kb_trace = (wiki_result.get("trace") or {}).get("llm_trace", []) if isinstance(wiki_result, dict) else []
        return {
            "kb_result": wiki_result,
            "trace": {"actions": ["wiki_lookup"]},
            "tool_observations": [{
                "tool": "wiki_lookup",
                "status": str(wiki_result.get("grounding_status") or "not_found"),
                "source_refs": self._source_refs(wiki_result),
                "grounded_facts": list(wiki_result.get("grounded_facts") or []),
            }],
            "llm_trace": [item for item in kb_trace if isinstance(item, dict)],
        }

    @staticmethod
    def _source_refs(value: dict[str, Any]) -> list[str]:
        refs = value.get("source_refs") if isinstance(value, dict) else []
        return [str(ref) for ref in refs if str(ref).strip()] if isinstance(refs, list) else []


class WikiLookupTool:
    """Adapter that preserves the existing LLM Wiki reader behind one agent tool."""

    def __init__(self, *, retrieval, kb_agent, knowledge_backend: str, knowledge_root: str) -> None:
        self.retrieval = retrieval
        self.kb_agent = kb_agent
        self.knowledge_backend = knowledge_backend
        self.knowledge_root = knowledge_root

    def lookup(self, *, text: str, context: dict) -> dict[str, Any]:
        retrieval = self.retrieval.retrieve(
            text,
            self.knowledge_backend,
            self.knowledge_root,
            current_query=text,
        )
        if retrieval.get("kb_architecture") != "llm_wiki" or retrieval.get("kb_mode") != "llm_wiki_catalog":
            return {"grounding_status": "not_found", "source_refs": [], "reason": "invalid_llm_wiki_catalog"}
        return self.kb_agent.read(
            text,
            retrieval.get("kb_snippets", []),
            conversation_context=context,
            require_coverage_review=True,
        )
