from __future__ import annotations

from typing import Any, Protocol


class ActionAgent(Protocol):
    def next_action(self, **kwargs: object) -> dict[str, Any]: ...


class WikiLookup(Protocol):
    def lookup(self, *, text: str, context: dict) -> dict[str, Any]: ...


class AgentLoopService:
    """Select at most one optional tool; customer text always belongs to the finalizer."""

    def __init__(self, *, agent: ActionAgent, wiki_lookup: WikiLookup) -> None:
        self.agent = agent
        self.wiki_lookup = wiki_lookup

    def run(self, *, text: str, context: dict) -> dict[str, Any]:
        action = self.agent.next_action(
            text=text,
            context=context,
            tool_observations=[],
            allowed_actions=["wiki_lookup"],
            iteration=1,
        )
        llm_trace = [item for item in action.get("llm_trace", []) if isinstance(item, dict)]
        name = str(action.get("action") or "").strip()
        if name != "wiki_lookup":
            return {
                "kb_result": {},
                "trace": {"actions": ["tool_not_used:unsupported_action"]},
                "tool_observations": [{
                    "tool": "agent_action",
                    "status": "not_used",
                    "reason": "unsupported_action",
                }],
                "llm_trace": llm_trace,
            }

        wiki_result = self.wiki_lookup.lookup(text=text, context=context)
        kb_trace = (wiki_result.get("trace") or {}).get("llm_trace", []) if isinstance(wiki_result, dict) else []
        llm_trace.extend(item for item in kb_trace if isinstance(item, dict))
        return {
            "kb_result": wiki_result,
            "trace": {"actions": ["wiki_lookup"]},
            "tool_observations": [{
                "tool": "wiki_lookup",
                "status": str(wiki_result.get("grounding_status") or "not_found"),
                "source_refs": self._source_refs(wiki_result),
                "grounded_facts": list(wiki_result.get("grounded_facts") or []),
            }],
            "llm_trace": llm_trace,
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


class DirectLLMActionAgent:
    def __init__(self, direct_llm) -> None:
        self.direct_llm = direct_llm

    def next_action(self, **kwargs: object) -> dict[str, Any]:
        next_action = getattr(self.direct_llm, "next_action", None)
        if not callable(next_action):
            return {
                "action": "tool_unavailable",
                "reason": "planner_action_api_unavailable",
                "llm_trace": [],
            }
        result = next_action(**kwargs)
        arguments = result.get("arguments") if isinstance(result, dict) else {}
        return ({**result, **arguments} if isinstance(arguments, dict) else result)
