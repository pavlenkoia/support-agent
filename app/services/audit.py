from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

TRACE_PACKET_VERSION = "stage2-c5"
TRACE_PACKET_MAX_BYTES = 16384
INPUT_MAX_BYTES = 8192
MODEL_STEPS = {"customer_turn", "tool_result_selection", "navigation", "coverage_review", "grounded_extraction"}


def json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def bounded_capture(data: dict, *, max_bytes: int = INPUT_MAX_BYTES) -> dict:
    encoded = json_bytes(data)
    digest = hashlib.sha256(encoded).hexdigest()
    if len(encoded) > max_bytes:
        return {"status": "overflow", "size_bytes": len(encoded), "max_bytes": max_bytes, "sha256": digest}
    return {"status": "complete", "data": data, "size_bytes": len(encoded), "sha256": digest}


def capture_model_input(boundary: str, data: dict) -> dict:
    # Only actual factual/context inputs; prompts, HTTP envelopes and reasoning are excluded.
    allowed = {"user_message", "first_reply_in_dialogue", "conversation", "tool_observations", "knowledge_mode", "response_intent", "allowed_routes", "grounding_evidence", "tool_facts", "tool_observation", "protocol_version", "selector_phase", "tool_exchanges", "tool_state", "remaining_tool_calls"}
    return {"boundary": boundary, **bounded_capture({k: v for k, v in data.items() if k in allowed})}


def model_calls(trace: list[dict]) -> list[dict]:
    return [item for item in trace if item.get("entry_kind") == "model_call" or (
        "entry_kind" not in item and item.get("step") in MODEL_STEPS
        and any(k in item for k in ("attempts", "provider", "model", "duration_ms"))
    )]


def clean_llm_trace(trace: list[dict]) -> list[dict]:
    allowed = {"entry_kind", "call_id", "native_tool_call_id", "role", "step", "provider", "model", "duration_ms", "attempts", "usage", "input_packet", "route", "customer_reply_emitted"}
    return [{key: value for key, value in item.items() if key in allowed} for item in trace]


def trace_metrics(trace: list[dict]) -> dict:
    calls = model_calls(trace)
    attempts = [item.get("attempts") for item in calls]
    known = all(type(v) is int and v >= 0 for v in attempts)
    return {"logical_llm_call_count": len(calls), "provider_attempt_count": sum(attempts) if known else None}


def _ordered_actions(response_strategy: dict, calls: list[dict]) -> list[dict]:
    requests = [item for item in response_strategy.get("tool_requests", []) if isinstance(item, dict)]
    unused_calls = list(calls)
    structured = []
    for index, action in enumerate(response_strategy.get("agent_actions", []), start=1):
        request = next((item for item in requests if item.get("tool") == action), None)
        if request is not None:
            requests.remove(request)
        if action in {"wiki_lookup", "calendar_lookup"}:
            native_id = request.get("tool_call_id") if request else None
            selectors = [call for call in unused_calls if call.get("role") == "direct_llm" and call.get("step") in {"customer_turn", "tool_result_selection"}]
            candidates = [call for call in selectors if native_id and call.get("native_tool_call_id") == native_id]
            # Old packets did not carry native decision IDs. Only their single,
            # unambiguous initial selector can be associated without guessing.
            if not candidates and not any(call.get("native_tool_call_id") for call in selectors):
                candidates = [call for call in selectors if call.get("step") == "customer_turn"]
            matched = candidates[0] if len(candidates) == 1 else None
        else:
            candidates = [call for call in unused_calls if call.get("step") == action]
            matched = candidates[0] if len(candidates) == 1 else None
        if matched is not None:
            unused_calls.remove(matched)
        structured.append({
            "order": index, "action": action,
            "tool_call_id": request.get("tool_call_id") if request else None,
            "trace_local_id": matched.get("trace_local_id") if matched else None,
            "provider_call_id": matched.get("call_id") if matched else None,
            "native_tool_call_id": matched.get("native_tool_call_id") if matched else None,
            "model_call": {k: v for k, v in matched.items() if k != "input_packet"} if matched else None,
        })
    return structured


def build_trace_packet(*, case: dict, route: dict, outcome: dict, retrieval: dict, response_strategy: dict) -> dict:
    calls = [{**call, "trace_local_id": call.get("trace_local_id") or f"trace-call-{index}"} for index, call in enumerate(model_calls(response_strategy.get("llm_trace", [])), start=1)]
    agent_final_input = next((c["input_packet"] for c in reversed(calls) if c.get("role") == "direct_llm" and isinstance(c.get("input_packet"), dict)), {"status": "unavailable"})
    observations = response_strategy.get("tool_observations", [])
    statuses = [o.get("status") for o in observations if isinstance(o, dict)]
    evidence_status = "ready" if "ready" in statuses else (statuses[-1] if statuses else "not_started")
    packet = {
        "version": TRACE_PACKET_VERSION, "trace_id": uuid4().hex,
        "status": "complete" if agent_final_input.get("status") == "complete" else "incomplete",
        "source_turn": {"case_id": case["case_id"], "conversation_id": case["conversation_id"], **response_strategy.get("source_turn", {})},
        "ordered_actions": _ordered_actions(response_strategy, calls),
        "source_refs": route.get("source_refs", []),
        "tool_requests": response_strategy.get("tool_requests", []),
        "agent_final_input": agent_final_input,
        "model_calls": [{k:v for k,v in c.items() if k != "input_packet"} for c in calls],
        "wiki_status": retrieval.get("kb_status"), "evidence_status": evidence_status,
        "result": {"route": route["route"], "outcome_kind": route.get("outcome_kind"), "reason": route.get("reason"), "confidence": route.get("route_confidence"),
                   "text_sha256": text_sha256(outcome.get("outcome_payload", {}).get("response_text", "")),
                   "delivery_status": "not_recorded" if route["route"] != "retry_pending" else "not_applicable"},
    }
    encoded = json_bytes(packet)
    if len(encoded) > TRACE_PACKET_MAX_BYTES:
        reduced = {
            "version": TRACE_PACKET_VERSION, "trace_id": packet["trace_id"], "status": "overflow",
            "size_bytes": len(encoded), "max_bytes": TRACE_PACKET_MAX_BYTES,
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "source_turn": {"case_id": case["case_id"], "conversation_id": case["conversation_id"]},
            "result": dict(packet["result"]),
        }
        if len(json_bytes(reduced)) > TRACE_PACKET_MAX_BYTES:
            reason_bytes = json_bytes(reduced["result"]["reason"])
            reduced["result"]["reason"] = {
                "status": "overflow", "size_bytes": len(reason_bytes),
                "max_bytes": TRACE_PACKET_MAX_BYTES,
                "sha256": hashlib.sha256(reason_bytes).hexdigest(),
            }
        return reduced
    return packet


def bounded_strategy(strategy: dict) -> dict:
    bounded = dict(strategy)
    for key in ("tool_requests", "tool_observations", "llm_trace", "source_turn"):
        if key not in bounded:
            continue
        capture = bounded_capture({key: bounded[key]}, max_bytes=TRACE_PACKET_MAX_BYTES)
        if capture["status"] == "overflow":
            bounded[key] = {} if isinstance(bounded[key], dict) else []
            bounded[key + "_capture"] = capture
    return bounded


def build_audit_event(case: dict, route: dict, retrieval: dict, outcome: dict, turn_classification: dict, response_strategy: dict) -> dict:
    return {
        "case_id": case["case_id"], "conversation_id": case["conversation_id"], "user_id": case["user_id"],
        "turn_classifier": {"turn_type": turn_classification.get("turn_type"), "confidence": turn_classification.get("confidence"), "reason": turn_classification.get("reason")},
        "response_strategy": bounded_strategy(response_strategy), "route": route["route"], "route_reason": route["route_reason"], "route_confidence": route["route_confidence"],
        "kb_status": retrieval["kb_status"], "kb_skip_reason": retrieval.get("kb_skip_reason"),
        "outcome_type": outcome["outcome_type"], "outcome_status": outcome["outcome_status"], "outcome_kind": route.get("outcome_kind"), "source_refs": route.get("source_refs", []),
        **trace_metrics(response_strategy.get("llm_trace", [])),
        "trace_packet": build_trace_packet(case=case,route=route,outcome=outcome,retrieval=retrieval,response_strategy=response_strategy),
        "timestamp": datetime.now(UTC).isoformat(),
    }
