from __future__ import annotations

from typing import Any, Protocol


class WikiLookup(Protocol):
    def lookup(self, *, text: str, context: dict, tool_request: dict[str, str]) -> dict[str, Any]: ...


class CalendarLookup(Protocol):
    def lookup(self, *, text: str, context: dict, tool_request: dict[str, str]) -> dict[str, Any]: ...


class UnifiedTurnModel(Protocol):
    def begin_turn(self, *, text: str, context: dict) -> dict[str, Any]: ...


class UnifiedTurnService:
    """One customer-facing model turn may return text or request the Wiki tool."""

    def __init__(self, *, model: UnifiedTurnModel, wiki_lookup: WikiLookup, calendar_lookup: CalendarLookup) -> None:
        self.model = model
        self.wiki_lookup = wiki_lookup
        self.calendar_lookup = calendar_lookup

    def run(self, *, text: str, context: dict) -> dict[str, Any]:
        turn = self.model.begin_turn(text=text, context=context)
        llm_trace = [item for item in turn.get("llm_trace", []) if isinstance(item, dict)] if isinstance(turn, dict) else []
        kind = str(turn.get("kind") or "") if isinstance(turn, dict) else ""
        if kind == "final":
            result = turn.get("result")
            if isinstance(result, dict):
                return {
                    "kb_result": {},
                    "final_result": result,
                    "trace": {"actions": ["final_response"]},
                    "tool_observations": [],
                    "llm_trace": llm_trace,
                }
        if kind == "calendar_lookup":
            tool_request = turn.get("tool_request")
            if not isinstance(tool_request, dict):
                return self._invalid_tool_request(llm_trace)
            calendar_result = self.calendar_lookup.lookup(text=text, context=context, tool_request=tool_request)
            observation = {
                "tool": "calendar_lookup",
                "status": str(calendar_result.get("status") or "not_found"),
                "summary": str(calendar_result.get("summary") or ""),
                "structured": calendar_result.get("structured") if isinstance(calendar_result.get("structured"), dict) else {},
            }
            result = self._continue_after_tool(text=text, context=context, observation=observation, action="calendar_lookup", tool_request=tool_request, tool_call_id=str(turn.get("tool_call_id") or "call_calendar"), llm_trace=llm_trace)
            result["tool_requests"] = [{"tool": kind, "tool_call_id": str(turn.get("tool_call_id") or "call_calendar"), "tool_request": dict(tool_request)}]
            return result
        if kind == "wiki_lookup":
            tool_request = turn.get("tool_request")
            if not isinstance(tool_request, dict):
                return self._invalid_tool_request(llm_trace)
            wiki_result = self.wiki_lookup.lookup(text=text, context=context, tool_request=tool_request)
            kb_trace = (wiki_result.get("trace") or {}).get("llm_trace", []) if isinstance(wiki_result, dict) else []
            llm_trace.extend(item for item in kb_trace if isinstance(item, dict))
            return {
                "kb_result": wiki_result,
                "trace": {"actions": ["wiki_lookup"]},
                "tool_requests": [{"tool": kind, "tool_call_id": turn.get("tool_call_id"), "tool_request": dict(tool_request)}],
                "tool_observations": [{
                    "tool": "wiki_lookup",
                    "status": str(wiki_result.get("grounding_status") or "not_found"),
                    "source_refs": self._source_refs(wiki_result),
                    "grounded_facts": list(wiki_result.get("grounded_facts") or []),
                }],
                "llm_trace": llm_trace,
            }
        return {
            "kb_result": {},
            "final_result": {
                "route": "retry_pending",
                "response_text": "",
                "confidence": 0.0,
                "reason": "unified_turn_invalid",
            },
            "trace": {"actions": ["invalid_turn"]},
            "tool_observations": [],
            "llm_trace": llm_trace,
        }

    def _continue_after_tool(
        self,
        *,
        text: str,
        context: dict,
        observation: dict[str, Any],
        action: str,
        tool_request: dict[str, str],
        tool_call_id: str,
        llm_trace: list[dict],
    ) -> dict[str, Any]:
        """Continue the same model-managed turn with the typed tool observation."""
        continuation_context = dict(context)
        existing_observations = context.get("tool_observations")
        previous = existing_observations if isinstance(existing_observations, list) else []
        continuation_context["tool_observations"] = [*previous, observation][-2:]
        continuation_method = getattr(self.model, "continue_after_tool", None)
        if callable(continuation_method):
            continuation = continuation_method(
                text=text, context=continuation_context, tool_name=action, tool_call_id=tool_call_id,
                tool_request=tool_request, observation=observation,
            )
        else:
            continuation = self.model.begin_turn(text=text, context=continuation_context)
        continuation_trace = continuation.get("llm_trace", []) if isinstance(continuation, dict) else []
        llm_trace.extend(item for item in continuation_trace if isinstance(item, dict))
        if not isinstance(continuation, dict) or str(continuation.get("kind") or "") != "final":
            return self._invalid_tool_request(llm_trace)
        final_result = continuation.get("result")
        if not isinstance(final_result, dict):
            return self._invalid_tool_request(llm_trace)
        return {
            "kb_result": {},
            "final_result": final_result,
            "trace": {"actions": [action, "final_response"]},
            "tool_observations": [observation],
            "llm_trace": llm_trace,
        }

    @staticmethod
    def _invalid_tool_request(llm_trace: list[dict]) -> dict[str, Any]:
        return {
            "kb_result": {},
            "final_result": {"route": "retry_pending", "response_text": "", "confidence": 0.0, "reason": "tool_request_invalid"},
            "trace": {"actions": ["invalid_tool_request"]},
            "tool_observations": [],
            "llm_trace": llm_trace,
        }

    @staticmethod
    def _source_refs(value: dict[str, Any]) -> list[str]:
        refs = value.get("source_refs") if isinstance(value, dict) else []
        return [str(ref) for ref in refs if str(ref).strip()] if isinstance(refs, list) else []


class CalendarLookupTool:
    """Adapter for calendar-only facts selected by the native tool loop."""

    def __init__(self, *, runtime) -> None:
        self.runtime = runtime

    def lookup(self, *, text: str, context: dict, tool_request: dict[str, str]) -> dict[str, Any]:
        _ = text
        return self.runtime.lookup_calendar(
            date_expression=tool_request["date_expression"],
            conversation_context=context,
        )


class WikiLookupTool:
    """Adapter that preserves the existing LLM Wiki reader behind one agent tool."""

    def __init__(self, *, retrieval, kb_agent, knowledge_backend: str, knowledge_root: str) -> None:
        self.retrieval = retrieval
        self.kb_agent = kb_agent
        self.knowledge_backend = knowledge_backend
        self.knowledge_root = knowledge_root

    def lookup(self, *, text: str, context: dict, tool_request: dict[str, str]) -> dict[str, Any]:
        query = tool_request["query"]
        scoped_context = dict(context)
        scoped_context["tool_request"] = dict(tool_request)
        retrieval = self.retrieval.retrieve(
            query,
            self.knowledge_backend,
            self.knowledge_root,
            current_query=query,
        )
        if retrieval.get("kb_architecture") != "llm_wiki" or retrieval.get("kb_mode") != "llm_wiki_catalog":
            return {"grounding_status": "not_found", "source_refs": [], "reason": "invalid_llm_wiki_catalog"}
        return self.kb_agent.read(
            query,
            retrieval.get("kb_snippets", []),
            conversation_context=scoped_context,
            require_coverage_review=True,
        )
