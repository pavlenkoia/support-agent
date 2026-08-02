from __future__ import annotations

from typing import Any

from app.services.direct_llm import DirectLLMService
from app.services.kb_agent import KBAgentService
from app.services.policy import PolicyService
from app.services.retrieval import RetrievalService
from app.services.tool_runtime import ToolRuntimeService


class OrchestratorService:
    def __init__(
        self,
        *,
        retrieval: RetrievalService,
        kb_agent: KBAgentService,
        direct_llm: DirectLLMService,
        policy: PolicyService,
        tool_runtime: ToolRuntimeService,
        max_iterations: int = 3,
    ) -> None:
        self.retrieval = retrieval
        self.kb_agent = kb_agent
        self.direct_llm = direct_llm
        self.policy = policy
        self.tool_runtime = tool_runtime
        self.max_iterations = max_iterations

    def run(
        self,
        *,
        text: str,
        context: dict,
        knowledge_backend: str,
        knowledge_root: str,
        knowledge_query: str,
    ) -> dict[str, Any]:
        loop_trace: list[dict[str, Any]] = []
        tool_observations: list[dict[str, Any]] = []
        tool_trace: list[dict[str, Any]] = []
        retrieval: dict[str, Any] = {"kb_status": "not_started", "kb_snippets": [], "kb_skip_reason": None}
        kb_result: dict[str, Any] = {}
        planner_trace: list[dict[str, Any]] = []
        final_reply: dict[str, Any] | None = None

        for iteration in range(1, self.max_iterations + 1):
            planner = self._plan_next_action(text, context, retrieval, tool_observations)
            planner_action = str(planner.get("action") or "cannot_answer")
            planner_trace.append({"iteration": iteration, **planner})

            if planner_action == "out_of_scope" and self._has_prior_assistant_reply(context):
                planner_action = "read_kb"
                loop_trace.append(
                    {
                        "iteration": iteration,
                        "action": "planner_override",
                        "reason": "contextual_followup_requires_kb",
                        "requested_action": planner.get("action"),
                        "effective_action": planner_action,
                    }
                )

            if planner_action == "social_reply":
                final_reply = self._finalize_social_reply(text, context)
                loop_trace.append(
                    {
                        "iteration": iteration,
                        "action": "social_reply",
                        "reason": final_reply.get("reason", "planner_social_reply"),
                    }
                )
                break

            if planner_action == "out_of_scope":
                final_reply = self._finalize_out_of_scope(planner)
                loop_trace.append(
                    {
                        "iteration": iteration,
                        "action": "out_of_scope",
                        "reason": final_reply.get("reason", "planner_out_of_scope"),
                    }
                )
                break

            if retrieval.get("kb_status") != "found" and planner_action not in {"read_kb", "use_tool"}:
                planner_action = "read_kb"
                loop_trace.append(
                    {
                        "iteration": iteration,
                        "action": "planner_override",
                        "reason": "mandatory_kb_before_customer_reply",
                        "requested_action": planner.get("action"),
                        "effective_action": planner_action,
                    }
                )

            if planner_action == "use_tool":
                tool_batch = self.tool_runtime.collect(
                    text=text,
                    kb_hits=retrieval.get("kb_snippets", []),
                    conversation_context=context,
                )
                if tool_batch.get("tool_status") == "used":
                    self._merge_tool_results(tool_observations, tool_trace, tool_batch)
                    loop_trace.append(
                        {
                            "iteration": iteration,
                            "action": "use_tool",
                            "reason": planner.get("reason", "planner_use_tool"),
                        }
                    )
                else:
                    loop_trace.append(
                        {
                            "iteration": iteration,
                            "action": "use_tool_skipped",
                            "reason": "planner_requested_but_no_tool_match",
                        }
                    )
                continue

            if planner_action == "read_kb":
                retrieval_query = self._augment_query_with_tool_results(knowledge_query or text, tool_observations)
                retrieval = self.retrieval.retrieve(
                    retrieval_query,
                    knowledge_backend,
                    knowledge_root,
                    current_query=text,
                )
                loop_trace.append(
                    {
                        "iteration": iteration,
                        "action": "read_kb",
                        "reason": planner.get("reason", "mandatory_kb_lookup"),
                        "kb_status": retrieval.get("kb_status"),
                    }
                )

                post_kb_tools = self.tool_runtime.collect(
                    text=text,
                    kb_hits=retrieval.get("kb_snippets", []),
                    conversation_context=context,
                )
                if post_kb_tools.get("tool_status") == "used":
                    self._merge_tool_results(tool_observations, tool_trace, post_kb_tools)
                    loop_trace.append(
                        {
                            "iteration": iteration,
                            "action": "use_tool",
                            "reason": "post_kb_tool_check",
                        }
                    )
                continue

            if planner_action == "ask_clarification":
                final_reply = {
                    "route": "clarification_requested",
                    "response_text": str(planner.get("clarification_question") or self.policy.render_cannot_answer()),
                    "confidence": float(planner.get("confidence", 0.0)),
                    "reason": str(planner.get("reason") or "planner_clarification"),
                }
                loop_trace.append(
                    {
                        "iteration": iteration,
                        "action": "clarification_requested",
                        "reason": final_reply["reason"],
                    }
                )
                break

            kb_result = self._ensure_kb_result(text, context, retrieval, tool_observations, kb_result, loop_trace, iteration)
            retrieval, kb_result = self._retry_missing_grounding(
                text=text,
                context=context,
                retrieval=retrieval,
                kb_result=kb_result,
                knowledge_backend=knowledge_backend,
                knowledge_root=knowledge_root,
                tool_observations=tool_observations,
                loop_trace=loop_trace,
                iteration=iteration,
            )
            final_reply = self._finalize_reply(
                text=text,
                context=context,
                retrieval=retrieval,
                kb_result=kb_result,
                tool_observations=tool_observations,
                planner_action=planner_action,
                planner_reason=str(planner.get("reason") or ""),
            )
            loop_trace.append(
                {
                    "iteration": iteration,
                    "action": final_reply.get("route", "cannot_answer"),
                    "reason": final_reply.get("reason", "prompt_runtime"),
                    "planner_action": planner_action,
                }
            )
            break

        if final_reply is None:
            kb_result = self._ensure_kb_result(text, context, retrieval, tool_observations, kb_result, loop_trace, self.max_iterations)
            retrieval, kb_result = self._retry_missing_grounding(
                text=text,
                context=context,
                retrieval=retrieval,
                kb_result=kb_result,
                knowledge_backend=knowledge_backend,
                knowledge_root=knowledge_root,
                tool_observations=tool_observations,
                loop_trace=loop_trace,
                iteration=self.max_iterations,
            )
            final_reply = self._finalize_reply(
                text=text,
                context=context,
                retrieval=retrieval,
                kb_result=kb_result,
                tool_observations=tool_observations,
                planner_action="max_iterations_fallback",
                planner_reason="max_iterations_fallback",
            )
            loop_trace.append(
                {
                    "iteration": self.max_iterations,
                    "action": final_reply.get("route", "cannot_answer"),
                    "reason": final_reply.get("reason", "prompt_runtime"),
                    "planner_action": "max_iterations_fallback",
                }
            )

        final_route = str(final_reply.get("route") or "cannot_answer")
        raw_response_text = "" if final_route == "retry_pending" else str(final_reply.get("response_text") or self.policy.render_cannot_answer())
        response_text = "" if final_route == "retry_pending" else self.policy.finalize_customer_text(
            raw_response_text,
            first_reply_in_dialogue=self._is_first_reply(context),
        )
        route = {
            "route": final_route,
            "route_reason": str(final_reply.get("reason") or "prompt_runtime"),
            "route_confidence": float(final_reply.get("confidence", 0.0)),
            "reply": {
                "response_text": response_text,
                "reason": str(final_reply.get("reason") or "prompt_runtime"),
                "tool_observations": tool_observations,
            },
        }

        direct_llm_trace = list(final_reply.get("llm_trace") or [])
        kb_agent_trace = kb_result.get("trace", {})
        kb_llm_trace = list(kb_agent_trace.get("llm_trace") or [])
        aggregated_llm_trace = self._aggregate_llm_trace(planner_trace, kb_llm_trace, direct_llm_trace)

        response_strategy = {
            "loop_mode": "agentic_bounded_loop_with_kb_agent",
            "final_action": route["route"],
            "knowledge_path": retrieval.get("kb_status", "not_found"),
            "kb_agent_mode": kb_result.get("kb_mode", "unknown"),
            "kb_agent_grounding_status": kb_result.get("grounding_status", "not_found"),
            "kb_agent_trace": kb_agent_trace,
            "planner_trace": planner_trace,
            "direct_llm_trace": direct_llm_trace,
            "tool_trace": tool_trace,
            "llm_trace": aggregated_llm_trace,
            "llm_usage_summary": self._summarize_llm_trace(aggregated_llm_trace),
            "steps": [item["action"] for item in loop_trace],
            "knowledge_query": self._augment_query_with_tool_results(knowledge_query or text, tool_observations),
        }
        return {
            "retrieval": retrieval,
            "kb_result": kb_result,
            "route": route,
            "response_strategy": response_strategy,
            "loop_trace": loop_trace,
        }

    def _plan_next_action(
        self,
        text: str,
        context: dict,
        retrieval: dict[str, Any],
        tool_observations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not tool_observations and self.tool_runtime.matches_calendar_query(text):
            return {
                "action": "use_tool",
                "scope_status": "in_scope",
                "confidence": 1.0,
                "reason": "system_calendar_query_match",
                "clarification_question": "",
            }
        if hasattr(self.direct_llm, "assess_request"):
            return self.direct_llm.assess_request(
                text,
                conversation_context={**context, "tool_observations": tool_observations},
                retrieval=retrieval,
                tool_observations=tool_observations,
            )
        if retrieval.get("kb_status") != "found":
            if self.tool_runtime.collect(text=text, kb_hits=retrieval.get("kb_snippets", []), conversation_context=context).get("tool_status") == "used" and not tool_observations:
                return {"action": "use_tool", "scope_status": "in_scope", "confidence": 0.5, "reason": "fallback_date_tool_first", "clarification_question": ""}
            return {"action": "read_kb", "scope_status": "in_scope", "confidence": 0.5, "reason": "fallback_mandatory_kb", "clarification_question": ""}
        return {"action": "answer_from_kb", "scope_status": "in_scope", "confidence": 0.5, "reason": "fallback_answer_from_kb", "clarification_question": ""}

    def _finalize_reply(
        self,
        *,
        text: str,
        context: dict,
        retrieval: dict[str, Any],
        kb_result: dict[str, Any],
        tool_observations: list[dict[str, Any]],
        planner_action: str,
        planner_reason: str,
    ) -> dict[str, Any]:
        if kb_result.get("grounding_status") == "retry_pending":
            return {
                "route": "retry_pending",
                "response_text": "",
                "confidence": 0.0,
                "reason": str(kb_result.get("reason") or "kb_agent_transport_failure"),
                "llm_trace": [],
            }
        if kb_result.get("grounding_status") != "ready":
            return {
                "route": "cannot_answer",
                "response_text": self.policy.render_cannot_answer(),
                "confidence": 0.0,
                "reason": str(kb_result.get("reason") or "kb_agent_no_ready_grounding"),
                "llm_trace": [],
            }
        if hasattr(self.direct_llm, "respond"):
            return self.direct_llm.respond(
                text,
                kb_result,
                conversation_context={
                    **context,
                    "tool_observations": tool_observations,
                    "planner_action": planner_action,
                    "planner_reason": planner_reason,
                },
                tool_observations=tool_observations,
                first_reply_in_dialogue=self._is_first_reply(context),
            )
        legacy = self.direct_llm.answer(
            text,
            kb_result.get("answer_context", retrieval.get("kb_snippets", [])),
            allow_general_without_kb=False,
            conversation_context={
                **context,
                "tool_observations": tool_observations,
                "planner_action": planner_action,
                "planner_reason": planner_reason,
            },
        )
        return {
            "route": "answer" if legacy.get("decision") == "answer" else "cannot_answer",
            "response_text": str(legacy.get("response_text") or ""),
            "confidence": float(legacy.get("confidence", 0.0)),
            "reason": str(legacy.get("reason") or "legacy_answer"),
        }

    def _finalize_social_reply(self, text: str, context: dict) -> dict[str, Any]:
        if hasattr(self.direct_llm, "respond_social"):
            return self.direct_llm.respond_social(text, conversation_context=context)
        return {
            "route": "answer",
            "response_text": "Пожалуйста!",
            "confidence": 1.0,
            "reason": "social_reply_legacy_fallback",
            "llm_trace": [],
        }

    def _finalize_out_of_scope(self, planner: dict[str, Any]) -> dict[str, Any]:
        """Planner-approved domain boundary must not depend on final LLM generation."""
        return {
            "route": "out_of_scope",
            "response_text": self.policy.render_out_of_scope(),
            "confidence": float(planner.get("confidence") or 0.0),
            "reason": str(planner.get("reason") or "planner_out_of_scope"),
            "llm_trace": [],
        }

    def _retry_missing_grounding(
        self,
        *,
        text: str,
        context: dict,
        retrieval: dict,
        kb_result: dict,
        knowledge_backend: str,
        knowledge_root: str,
        tool_observations: list[dict[str, Any]],
        loop_trace: list[dict[str, Any]],
        iteration: int,
    ) -> tuple[dict, dict]:
        if kb_result.get("grounding_status") != "not_found":
            return retrieval, kb_result
        expanded = self.retrieval.expand_for_grounding(
            retrieval,
            current_query=text,
            knowledge_backend=knowledge_backend,
            knowledge_root=knowledge_root,
        )
        if len(expanded.get("kb_snippets", [])) <= len(retrieval.get("kb_snippets", [])):
            return retrieval, kb_result
        retried = self.kb_agent.read(
            text,
            expanded.get("kb_snippets", []),
            conversation_context={**context, "tool_observations": tool_observations},
        )
        loop_trace.append(
            {
                "iteration": iteration,
                "action": "kb_agent_grounding_retry",
                "reason": retried.get("grounding_status", "not_found"),
                "added_source_refs": [
                    item.get("source_ref")
                    for item in expanded.get("kb_snippets", [])
                    if item.get("source_ref") not in {
                        source.get("source_ref") for source in retrieval.get("kb_snippets", [])
                    }
                ],
            }
        )
        return expanded, retried

    def _ensure_kb_result(
        self,
        text: str,
        context: dict,
        retrieval: dict[str, Any],
        tool_observations: list[dict[str, Any]],
        kb_result: dict[str, Any],
        loop_trace: list[dict[str, Any]],
        iteration: int,
    ) -> dict[str, Any]:
        if kb_result:
            return kb_result
        kb_result = self.kb_agent.read(
            text,
            retrieval.get("kb_snippets", []),
            conversation_context={**context, "tool_observations": tool_observations},
        )
        loop_trace.append(
            {
                "iteration": iteration,
                "action": "kb_agent_read",
                "reason": kb_result.get("grounding_status", "not_found"),
            }
        )
        return kb_result

    def _is_first_reply(self, context: dict) -> bool:
        return not self._has_prior_assistant_reply(context)

    @staticmethod
    def _has_prior_assistant_reply(context: dict) -> bool:
        return any(
            str(item.get("role") or "") == "assistant"
            for item in context.get("recent_messages", [])
            if isinstance(item, dict)
        )

    def _augment_query_with_tool_results(self, knowledge_query: str, tool_observations: list[dict[str, Any]]) -> str:
        if not tool_observations:
            return knowledge_query
        summaries = [str(item.get("summary") or "").strip() for item in tool_observations if str(item.get("summary") or "").strip()]
        if not summaries:
            return knowledge_query
        return f"{knowledge_query}\n\nTool observations:\n" + "\n".join(f"- {item}" for item in summaries)

    def _merge_tool_results(
        self,
        tool_observations: list[dict[str, Any]],
        tool_trace: list[dict[str, Any]],
        tool_batch: dict[str, Any],
    ) -> None:
        for item in tool_batch.get("tool_results", []):
            if item not in tool_observations:
                tool_observations.append(item)
        tool_trace.extend(tool_batch.get("tool_trace", []))

    def _aggregate_llm_trace(
        self,
        planner_trace: list[dict[str, Any]],
        kb_llm_trace: list[dict[str, Any]],
        direct_llm_trace: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        aggregated: list[dict[str, Any]] = []
        for planner_item in planner_trace:
            for llm_item in planner_item.get("llm_trace", []) or []:
                aggregated.append({"iteration": planner_item.get("iteration"), **llm_item})
        aggregated.extend(kb_llm_trace)
        aggregated.extend(direct_llm_trace)
        return aggregated

    def _summarize_llm_trace(self, llm_trace: list[dict[str, Any]]) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "call_count": 0,
            "total_duration_ms": 0.0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "missing_usage_calls": 0,
            "failover_count": 0,
            "failover_calls": 0,
        }
        by_role: dict[str, dict[str, Any]] = {}
        for item in llm_trace:
            role = str(item.get("role") or "unknown")
            role_bucket = by_role.setdefault(
                role,
                {
                    "call_count": 0,
                    "total_duration_ms": 0.0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "missing_usage_calls": 0,
                    "failover_count": 0,
                    "failover_calls": 0,
                },
            )
            for bucket in (summary, role_bucket):
                bucket["call_count"] += 1
                bucket["total_duration_ms"] += float(item.get("duration_ms") or 0.0)
                bucket["failover_count"] += int(item.get("failover_count") or 0)
                if item.get("used_failover"):
                    bucket["failover_calls"] += 1
            usage = item.get("usage") or {}
            pt = usage.get("prompt_tokens")
            ct = usage.get("completion_tokens")
            tt = usage.get("total_tokens")
            if all(value is None for value in (pt, ct, tt)):
                summary["missing_usage_calls"] += 1
                role_bucket["missing_usage_calls"] += 1
                continue
            for bucket in (summary, role_bucket):
                bucket["prompt_tokens"] += int(pt or 0)
                bucket["completion_tokens"] += int(ct or 0)
                bucket["total_tokens"] += int(tt or 0)
        summary["total_duration_ms"] = round(summary["total_duration_ms"], 3)
        for bucket in by_role.values():
            bucket["total_duration_ms"] = round(bucket["total_duration_ms"], 3)
        summary["by_role"] = by_role
        return summary
