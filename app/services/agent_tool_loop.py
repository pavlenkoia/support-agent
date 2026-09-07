from __future__ import annotations

import json
from typing import Any, Protocol


class WikiLookup(Protocol):
    def lookup(self, *, text: str, context: dict, tool_request: dict[str, str]) -> dict[str, Any]: ...


class CalendarLookup(Protocol):
    def lookup(self, *, text: str, context: dict, tool_request: dict[str, str]) -> dict[str, Any]: ...


class UnifiedTurnModel(Protocol):
    def begin_turn(self, *, text: str, context: dict) -> dict[str, Any]: ...


class UnifiedTurnService:
    """Collect bounded native tool evidence; never write customer text."""

    def __init__(self, *, model: UnifiedTurnModel, wiki_lookup: WikiLookup, calendar_lookup: CalendarLookup) -> None:
        self.model = model
        self.wiki_lookup = wiki_lookup
        self.calendar_lookup = calendar_lookup

    def run(self, *, text: str, context: dict) -> dict[str, Any]:
        from app.services.direct_llm import DirectLLMService

        actions: list[str] = []
        requests: list[dict] = []
        observations: list[dict] = []
        history: list[dict] = []
        trace: list[dict] = []
        kb_result: dict = {}

        def packet() -> dict[str, Any]:
            return {"kb_result": kb_result, "trace": {"actions": list(actions)},
                    "tool_requests": list(requests), "tool_observations": list(observations), "llm_trace": list(trace)}

        def failure(reason: str) -> dict[str, Any]:
            return {**packet(), "final_result": {"route": "retry_pending", "response_text": "", "confidence": None, "reason": reason}}

        current = self.model.begin_turn(text=text, context=context)
        for decision_index in range(3):
            if not isinstance(current, dict):
                return failure("invalid_selector_output")
            trace.extend(item for item in current.get("llm_trace", []) if isinstance(item, dict))
            kind = current.get("kind")
            if kind == "finalization_requested":
                if current.get("response_intent") not in {"answer", "social_reply", "clarification", "missing_grounding"}:
                    return failure("invalid_selector_output")
                return {**packet(), "finalization_requested": {"response_intent": current["response_intent"], "reason": current.get("reason", "")}}
            if kind == "final":
                result = current.get("result")
                if isinstance(result, dict) and result.get("route") == "retry_pending":
                    return {**packet(), "final_result": result}
                return failure("invalid_selector_output")
            if kind not in {"wiki_lookup", "calendar_lookup"}:
                return failure("tool_request_invalid")
            if decision_index == 2:
                return failure("tool_budget_exceeded")
            if kind in actions:
                return failure("tool_duplicate_call")
            request = current.get("tool_request")
            if DirectLLMService._validated_legacy_tool_request(kind, request) is None:
                return failure("tool_request_invalid")
            ident = current.get("tool_call_id")
            if not isinstance(ident, str) or not ident.strip() or any(r["tool_call_id"] == ident for r in requests):
                return failure("tool_call_id_invalid")
            requests.append({"tool": kind, "tool_call_id": ident, "tool_request": dict(request)})
            actions.append(kind)
            tool_context = {**context, "tool_observations": list(observations)}
            lookup = self.wiki_lookup if kind == "wiki_lookup" else self.calendar_lookup
            try:
                value = lookup.lookup(text=text, context=tool_context, tool_request=request)
            except (OSError, ValueError):
                return failure("tool_unavailable")
            if not isinstance(value, dict):
                return failure("tool_result_invalid")
            if kind == "wiki_lookup":
                kb_result = value
                kb_trace = value.get("trace")
                if isinstance(kb_trace, dict):
                    trace.extend(t for t in kb_trace.get("llm_trace", []) if isinstance(t, dict))
                status = value.get("grounding_status")
                observation = {"tool": kind, "status": status, "source_refs": self._source_refs(value),
                               "grounded_facts": value.get("grounded_facts", []) if status == "ready" else []}
            else:
                status = value.get("status")
                observation = {"tool": kind, "status": status, "summary": value.get("summary", ""), "structured": value.get("structured", {})}
            observations.append(observation)
            if status in {"unavailable", "llm_unavailable", "retry_pending"}:
                return failure("tool_unavailable")
            if status not in {"ready", "not_found"}:
                return failure("tool_result_invalid")
            history.extend([
                {"role": "assistant", "tool_calls": [{"id": ident, "type": "function", "function": {"name": kind, "arguments": json.dumps(request, ensure_ascii=False)}}]},
                {"role": "tool", "tool_call_id": ident, "content": json.dumps(observation, ensure_ascii=False)},
            ])
            continuation_context = {**context, "tool_observations": list(observations), "tool_requests": list(requests), "native_tool_messages": list(history)}
            continuation = getattr(self.model, "continue_after_tool", None)
            if callable(continuation):
                current = continuation(text=text, context=continuation_context, tool_name=kind, tool_call_id=ident, tool_request=request, observation=observation)
            else:
                current = self.model.begin_turn(text=text, context=continuation_context)
        return failure("tool_budget_exceeded")

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
