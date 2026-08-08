from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from app.core.config import settings
from app.services.system_prompt import SystemPromptService


class PolicyService:
    _GREETING_PATTERN = re.compile(
        r"^\s*(?:здравствуй(?:те)?|добрый\s+(?:день|вечер)|доброе\s+утро|привет(?:ствую)?|"
        r"доброго\s+времени\s+суток|рад(?:а)?\s+(?:вас\s+)?приветствовать)\b",
        flags=re.IGNORECASE,
    )
    _INTERNAL_OUTPUT_PATTERN = re.compile(
        r"(?:\bprofile\b|\bknowledgebase\b|\bdatetime\b|\btool\b|\bresult\b|\binput\b|\boutput\b|\bjson\b|"
        r"\broute\b|\bconfidence\b|\bпрофил\w*|баз[аы]\s+знани\w*|служебн\w*|"
        r"внутренн\w*|инструмент\w*|ход\s+рассужден\w*)",
        flags=re.IGNORECASE,
    )

    def __init__(self, profile_root: str | None = None, prompt_service: SystemPromptService | None = None) -> None:
        self.profile_root = Path(profile_root or settings.support_agent_profile_root)
        self.prompt_service = prompt_service or SystemPromptService()

    def load_profile(self) -> dict[str, Any]:
        profile_path = self.profile_root / "profile.yaml"
        if not profile_path.exists():
            return {}
        try:
            data = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def render_cannot_answer(self) -> str:
        prompt_fallback = self.prompt_service.render_cannot_answer()
        if prompt_fallback:
            return prompt_fallback

        profile = self.load_profile()
        templates = profile.get("response_templates", {}) if isinstance(profile, dict) else {}
        message = templates.get("cannot_answer") if isinstance(templates, dict) else None
        if isinstance(message, str) and message.strip():
            return message.strip()
        return "Сейчас не могу дать точный ответ на этот вопрос."

    def render_out_of_scope(self) -> str:
        profile = self.load_profile()
        templates = profile.get("response_templates", {}) if isinstance(profile, dict) else {}
        message = templates.get("out_of_scope") if isinstance(templates, dict) else None
        if isinstance(message, str) and message.strip():
            return message.strip()
        return "К сожалению, по этому вопросу я не смогу подсказать."

    @staticmethod
    def render_llm_unavailable() -> str:
        """Single non-technical terminal answer for exhausted LLM recovery."""
        return "Сейчас не удаётся подготовить ответ. Пожалуйста, повторите попытку немного позже."

    def finalize_customer_text(self, text: str, *, first_reply_in_dialogue: bool) -> str:
        """Apply one customer-facing boundary to every terminal route."""
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip().replace("**", "").replace("__", "")
        if not cleaned or self._INTERNAL_OUTPUT_PATTERN.search(cleaned) or any(char in cleaned for char in "[]{}"):
            cleaned = self.render_cannot_answer()
        if first_reply_in_dialogue and not self._GREETING_PATTERN.search(cleaned):
            cleaned = f"Здравствуйте! {cleaned}"
        return cleaned[:1000]

    def render_clarification(self, question: str | None = None) -> str:
        cleaned = str(question or "").strip()
        if cleaned:
            return cleaned
        return "Уточните, пожалуйста, ваш вопрос чуть точнее."
