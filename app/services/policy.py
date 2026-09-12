from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from app.core.config import settings
from app.services.agent_response_validation import clean_customer_text


class PolicyService:
    _GREETING_PATTERN = re.compile(
        r"^\s*(?:здравствуй(?:те)?|добрый\s+(?:день|вечер)|доброе\s+утро|привет(?:ствую)?|"
        r"доброго\s+времени\s+суток|рад(?:а)?\s+(?:вас\s+)?приветствовать)\b",
        flags=re.IGNORECASE,
    )
    _LEADING_GREETING_PATTERN = re.compile(
        r"^\s*(?:здравствуй(?:те)?|добрый\s+(?:день|вечер)|доброе\s+утро|привет(?:ствую)?|"
        r"доброго\s+времени\s+суток|рад(?:а)?\s+(?:вас\s+)?приветствовать)\b[!,.\s]*",
        flags=re.IGNORECASE,
    )

    def __init__(self, profile_root: str | None = None, prompt_service: object | None = None) -> None:
        _ = prompt_service
        self.profile_root = Path(profile_root or settings.support_agent_profile_root)

    def load_profile(self) -> dict[str, Any]:
        profile_path = self.profile_root / "profile.yaml"
        if not profile_path.exists():
            return {}
        try:
            data = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def no_answer_policy_evidence(self) -> list[dict[str, str]]:
        policy = self.load_profile().get("fallback_policy", {})
        no_answer = policy.get("no_answer", {}) if isinstance(policy, dict) else {}
        evidence = no_answer.get("evidence", {}) if isinstance(no_answer, dict) else {}
        source_ref = str(evidence.get("source_ref") or "").strip() if isinstance(evidence, dict) else ""
        text = str(evidence.get("text") or "").strip() if isinstance(evidence, dict) else ""
        return [{"source_ref": source_ref, "text": text}] if source_ref and text else []

    _INTERNAL_TRAILER_PATTERN = re.compile(
        r"(?:\n\s*)?(?:source_refs|evidence|grounding_evidence|tool_observations)\s*:\s*.*$",
        flags=re.IGNORECASE | re.DOTALL,
    )

    def finalize_customer_text(self, text: str, *, first_reply_in_dialogue: bool) -> str:
        """Apply deterministic presentation cleanup without composing meaning."""
        cleaned = clean_customer_text(text)
        # Reasoning-capable compatible models sometimes append their required
        # internal audit fields after the customer text. They are never client
        # content. Markdown code delimiters are likewise presentation noise.
        cleaned = self._INTERNAL_TRAILER_PATTERN.sub("", cleaned).replace("`", "").strip()
        if not cleaned:
            return ""
        cleaned = self._LEADING_GREETING_PATTERN.sub("", cleaned).strip()
        if first_reply_in_dialogue:
            cleaned = "Здравствуйте!" if not cleaned else f"Здравствуйте! {cleaned}"
        return cleaned

    def finalize_simple_customer_text(self, text: str, *, first_reply_in_dialogue: bool) -> str:
        return self.finalize_customer_text(text, first_reply_in_dialogue=first_reply_in_dialogue)
