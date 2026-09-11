"""Application-owned validation of untrusted final model output (no business rules)."""
from __future__ import annotations

from typing import Any

CLIENT_ROUTES = frozenset({"answer", "social_reply", "cannot_answer", "out_of_scope", "clarification_requested"})
# Plain-text adapters: Telegram Bot API sendMessage; VK API messages.send.
CHANNEL_TEXT_LIMITS = {"telegram": 4096, "vk": 9000}


def clean_customer_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().replace("**", "").replace("__", "")


def invalid_agent_response(reason: str = "invalid_agent_response") -> dict[str, Any]:
    return {"route": "retry_pending", "response_text": "", "confidence": None, "reason": reason}


def validate_agent_response(
    value: object, *, allow_technical: bool = False, channel: str | None = None,
) -> dict[str, Any]:
    """Validate schema before formatting; repeat with channel after greeting.

    ``allow_technical`` is only for an application-produced result at routing,
    never for a raw model envelope. Technical outcomes always discard text.
    """
    if not isinstance(value, dict):
        return invalid_agent_response()
    route = value.get("route")
    reason = value.get("reason")
    reason = reason.strip() if isinstance(reason, str) and reason.strip() else "prompt_runtime"
    if allow_technical and route == "retry_pending":
        return invalid_agent_response(reason)
    if not isinstance(route, str) or route not in CLIENT_ROUTES:
        return invalid_agent_response()
    text = clean_customer_text(value.get("response_text"))
    if not text or any(marker in text.casefold() for marker in ("knowledgebase result:", "kb_snippets", "tool_results")):
        return invalid_agent_response()
    confidence = value.get("confidence")
    if confidence is not None:
        # Range-check before float conversion also rejects huge integers safely.
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            return invalid_agent_response()
        confidence = float(confidence)
    limit = CHANNEL_TEXT_LIMITS.get(channel) if channel is not None else None
    if limit is not None and len(text) > limit:
        return invalid_agent_response("output_too_long")
    return {"route": route, "response_text": text, "confidence": confidence, "reason": reason}
