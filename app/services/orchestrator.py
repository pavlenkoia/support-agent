from __future__ import annotations

from typing import Any

from app.services.direct_llm import DirectLLMService
from app.services.policy import PolicyService
from app.services.retrieval import RetrievalService
from app.services.tool_runtime import ToolRuntimeService


class OrchestratorService:
    def __init__(
        self,
        *,
        retrieval: RetrievalService,
        direct_llm: DirectLLMService,
        policy: PolicyService,
        tool_runtime: ToolRuntimeService,
        max_iterations: int = 3,
    ) -> None:
        self.retrieval = retrieval
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
        turn_classification: dict,
        knowledge_query: str,
    ) -> dict[str, Any]:
        loop_trace: list[dict[str, Any]] = []

        if turn_classification.get("turn_type") == "social_turn":
            direct = self.direct_llm.answer(
                text,
                [],
                allow_general_without_kb=True,
                conversation_context=context,
            )
            response_strategy = {
                "loop_mode": "bounded_agent_loop",
                "classifier_path": "llm_turn_classifier",
                "final_action": "social_reply",
                "knowledge_path": "kb_skipped",
                "steps": ["social_turn"],
            }
            return {
                "retrieval": {
                    "kb_status": "skipped_social_turn",
                    "kb_skip_reason": "model_classified_social_turn",
                    "kb_snippets": [],
                },
                "route": {
                    "route": "answer",
                    "route_reason": "social_turn",
                    "route_confidence": float(direct.get("confidence", turn_classification.get("confidence", 0.0))),
                    "reply": direct,
                },
                "response_strategy": response_strategy,
                "loop_trace": loop_trace,
            }

        retrieval: dict[str, Any] = {"kb_status": "not_started", "kb_snippets": []}
        tool_observations: list[dict[str, Any]] = []
        tool_trace: list[dict[str, Any]] = []
        planning_text = knowledge_query or text

        for iteration in range(1, self.max_iterations + 1):
            plan = self.direct_llm.assess_request(
                planning_text,
                conversation_context=context,
                retrieval=retrieval,
                tool_observations=tool_observations,
            )
            loop_trace.append(
                {
                    "iteration": iteration,
                    "action": plan.get("action"),
                    "reason": plan.get("reason"),
                    "scope_status": plan.get("scope_status"),
                    "confidence": plan.get("confidence"),
                }
            )
            action = str(plan.get("action") or "cannot_answer")

            if action == "read_kb":
                retrieval = self.retrieval.retrieve(
                    knowledge_query,
                    knowledge_backend,
                    knowledge_root,
                )
                continue

            if action == "use_tool":
                tool_batch = self.tool_runtime.collect(
                    text=text,
                    kb_hits=retrieval.get("kb_snippets", []),
                    conversation_context=context,
                )
                tool_observations.extend(tool_batch.get("tool_results", []))
                tool_trace.extend(tool_batch.get("tool_trace", []))
                continue

            if action == "ask_clarification":
                clarification_text = self.policy.render_clarification(plan.get("clarification_question"))
                response_strategy = {
                    "loop_mode": "bounded_agent_loop",
                    "classifier_path": "llm_turn_classifier",
                    "final_action": "ask_clarification",
                    "knowledge_path": retrieval.get("kb_status", "not_started"),
                    "tool_trace": tool_trace,
                    "steps": [item["action"] for item in loop_trace],
                }
                return {
                    "retrieval": retrieval,
                    "route": {
                        "route": "clarification_requested",
                        "route_reason": str(plan.get("reason") or "clarification_required"),
                        "route_confidence": float(plan.get("confidence", 0.0)),
                        "reply": {
                            "response_text": clarification_text,
                            "reason": str(plan.get("reason") or "clarification_required"),
                            "tool_observations": tool_observations,
                        },
                    },
                    "response_strategy": response_strategy,
                    "loop_trace": loop_trace,
                }

            if action == "out_of_scope":
                response_strategy = {
                    "loop_mode": "bounded_agent_loop",
                    "classifier_path": "llm_turn_classifier",
                    "final_action": "out_of_scope",
                    "knowledge_path": retrieval.get("kb_status", "not_started"),
                    "tool_trace": tool_trace,
                    "steps": [item["action"] for item in loop_trace],
                }
                return {
                    "retrieval": retrieval,
                    "route": {
                        "route": "out_of_scope",
                        "route_reason": str(plan.get("reason") or "out_of_scope"),
                        "route_confidence": float(plan.get("confidence", 0.0)),
                        "reply": {
                            "response_text": self.policy.render_out_of_scope(),
                            "reason": str(plan.get("reason") or "out_of_scope"),
                            "tool_observations": tool_observations,
                        },
                    },
                    "response_strategy": response_strategy,
                    "loop_trace": loop_trace,
                }

            if action == "answer_from_kb":
                if retrieval.get("kb_status") == "not_started":
                    retrieval = self.retrieval.retrieve(
                        knowledge_query,
                        knowledge_backend,
                        knowledge_root,
                    )
                answer_text = knowledge_query
                if tool_observations:
                    answer_text += "\n\nRuntime observations:\n" + "\n".join(
                        f"- {item.get('summary', '')}" for item in tool_observations if item.get("summary")
                    )
                direct = self.direct_llm.answer(
                    answer_text,
                    retrieval.get("kb_snippets", []),
                    conversation_context={**context, "tool_observations": tool_observations},
                )
                if direct.get("decision") == "answer" and str(direct.get("response_text") or "").strip():
                    direct["tool_observations"] = tool_observations
                    response_strategy = {
                        "loop_mode": "bounded_agent_loop",
                        "classifier_path": "llm_turn_classifier",
                        "final_action": "answer_from_kb",
                        "knowledge_path": retrieval.get("kb_status", "not_started"),
                        "tool_trace": tool_trace,
                        "steps": [item["action"] for item in loop_trace],
                        "knowledge_query": knowledge_query,
                    }
                    return {
                        "retrieval": retrieval,
                        "route": {
                            "route": "answer",
                            "route_reason": str(direct.get("reason") or plan.get("reason") or "answer_from_kb"),
                            "route_confidence": float(direct.get("confidence", plan.get("confidence", 0.0))),
                            "reply": direct,
                        },
                        "response_strategy": response_strategy,
                        "loop_trace": loop_trace,
                    }
                plan = {**plan, "reason": str(direct.get("reason") or plan.get("reason") or "cannot_answer")}
                action = "cannot_answer"

            if action == "cannot_answer":
                response_strategy = {
                    "loop_mode": "bounded_agent_loop",
                    "classifier_path": "llm_turn_classifier",
                    "final_action": "cannot_answer",
                    "knowledge_path": retrieval.get("kb_status", "not_started"),
                    "tool_trace": tool_trace,
                    "steps": [item["action"] for item in loop_trace],
                }
                return {
                    "retrieval": retrieval,
                    "route": {
                        "route": "cannot_answer",
                        "route_reason": str(plan.get("reason") or "cannot_answer"),
                        "route_confidence": float(plan.get("confidence", 0.0)),
                        "reply": {
                            "response_text": self.policy.render_cannot_answer(),
                            "reason": str(plan.get("reason") or "cannot_answer"),
                            "tool_observations": tool_observations,
                        },
                    },
                    "response_strategy": response_strategy,
                    "loop_trace": loop_trace,
                }

        response_strategy = {
            "loop_mode": "bounded_agent_loop",
            "classifier_path": "llm_turn_classifier",
            "final_action": "cannot_answer",
            "knowledge_path": retrieval.get("kb_status", "not_started"),
            "tool_trace": tool_trace,
            "steps": [item["action"] for item in loop_trace],
            "stop_reason": "iteration_limit_reached",
        }
        return {
            "retrieval": retrieval,
            "route": {
                "route": "cannot_answer",
                "route_reason": "iteration_limit_reached",
                "route_confidence": 0.0,
                "reply": {
                    "response_text": self.policy.render_cannot_answer(),
                    "reason": "iteration_limit_reached",
                    "tool_observations": tool_observations,
                },
            },
            "response_strategy": response_strategy,
            "loop_trace": loop_trace,
        }
