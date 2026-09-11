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
    """Run one agent-owned, sequential native-tool turn.

    The model owns the semantic task: it may call tools repeatedly and returns
    the finished customer response only when it considers the evidence enough.
    This service only validates the wire protocol and executes registered tools.
    """

    MAX_SEQUENTIAL_TOOL_CALLS = 4

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
        for decision_index in range(self.MAX_SEQUENTIAL_TOOL_CALLS + 1):
            if not isinstance(current, dict):
                return failure("invalid_selector_output")
            trace.extend(item for item in current.get("llm_trace", []) if isinstance(item, dict))
            kind = current.get("kind")
            if kind == "direct_response":
                result = current.get("result")
                if isinstance(result, dict) and result.get("route") in {
                    "answer", "social_reply", "cannot_answer", "out_of_scope", "clarification_requested",
                } and (actions or result.get("route") == "social_reply"):
                    return {**packet(), "final_result": result}
                return failure("invalid_direct_response")

            if kind == "final":
                result = current.get("result")
                if isinstance(result, dict) and result.get("route") == "retry_pending":
                    return {**packet(), "final_result": result}
                return failure("invalid_selector_output")
            if kind not in {"wiki_lookup", "calendar_lookup"}:
                return failure("tool_request_invalid")
            if decision_index >= self.MAX_SEQUENTIAL_TOOL_CALLS:
                return failure("tool_budget_exceeded")
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
                if value.get("answer_evidence") is not None:
                    from app.services.answer_evidence import EvidenceValidationError, validate_answer_evidence
                    try:
                        observation["answer_evidence"] = validate_answer_evidence(
                            value["answer_evidence"], selected_source_refs=value.get("source_refs", []),
                        )
                    except EvidenceValidationError:
                        # A malformed extractor packet is not a customer fact.
                        # Preserve any already-collected native observations and
                        # let the model finish from their verified facts instead
                        # of treating a coverage verdict as a terminal answer
                        # decision.  Do not forward the malformed Wiki payload.
                        kb_result = {"grounding_status": "not_found"}
                        status = "not_found"
                        observation = {"tool": kind, "status": status, "source_refs": [], "grounded_facts": []}
            else:
                status = value.get("status")
                observation = {"tool": kind, "status": status, "summary": value.get("summary", ""), "structured": value.get("structured", {})}
            observations.append(observation)
            if status in {"unavailable", "llm_unavailable", "retry_pending"}:
                reason = value.get("reason")
                return failure(reason if isinstance(reason, str) and reason else "tool_unavailable")
            if status not in {"ready", "not_found"}:
                return failure("tool_result_invalid")
            history.extend([
                {"role": "assistant", "tool_calls": [{"id": ident, "type": "function", "function": {"name": kind, "arguments": json.dumps(request, ensure_ascii=False)}}]},
                {"role": "tool", "tool_call_id": ident, "content": json.dumps(observation, ensure_ascii=False)},
            ])
            # The selector sees literal dialogue and ordered tool results.  A
            # model-authored lookup query is retrieval-only and must never
            # become a rewritten customer question for later decisions.
            continuation_context = {**context, "tool_observations": list(observations), "tool_requests": list(requests), "native_tool_messages": list(history)}
            continuation = getattr(self.model, "continue_after_tool", None)
            if callable(continuation):
                current = continuation(text=text, context=continuation_context, tool_name=kind, tool_call_id=ident, tool_request=request, observation=observation)
            else:
                current = self.model.begin_turn(text=text, context=continuation_context)
        return failure("tool_budget_exceeded")

    @staticmethod
    def _has_complete_answer_evidence(evidence: Any) -> bool:
        """A validated full Wiki packet can proceed straight to answer generation.

        A second selector pass adds no evidence and can only turn an already
        grounded answer into a transport failure. Calendar-dependent turns are
        unaffected because they must already have supplied complete coverage.
        """
        if not isinstance(evidence, dict):
            return False
        coverage = evidence.get("coverage")
        return (
            evidence.get("acquisition_status") == "ready"
            and isinstance(coverage, dict)
            and coverage.get("status") == "full"
            and bool(evidence.get("facts"))
            and not coverage.get("missing_parts")
            and not coverage.get("conflicts")
            and not coverage.get("unresolved_constraints")
        )

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
        date_expressions = tool_request.get("date_expressions")
        return self.runtime.lookup_calendar(
            date_expressions=date_expressions if isinstance(date_expressions, list) else None,
            date_expression=tool_request.get("date_expression"),
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
        scoped_context["user_question"] = text
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
