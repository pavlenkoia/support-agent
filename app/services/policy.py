from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.core.config import settings


class PolicyService:
    def __init__(self, profile_root: str | None = None) -> None:
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

    def render_cannot_answer(self) -> str:
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

        in_scope = []
        scope = profile.get("scope", {}) if isinstance(profile, dict) else {}
        if isinstance(scope, dict):
            raw_in_scope = scope.get("in_scope", [])
            if isinstance(raw_in_scope, list):
                in_scope = [str(item).strip() for item in raw_in_scope if str(item).strip()]

        if in_scope:
            short_list = ", ".join(in_scope[:3])
            return f"Я отвечаю только по вопросам этого профиля: {short_list}."
        return "Я отвечаю только по вопросам этого профиля."

    def render_clarification(self, question: str | None = None) -> str:
        cleaned = str(question or "").strip()
        if cleaned:
            return cleaned
        return "Уточните, пожалуйста, ваш вопрос чуть точнее."
