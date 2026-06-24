from __future__ import annotations

import json

from app.core.config import settings
from app.integrations.llm.base import BaseLLMClient
from app.integrations.llm.factory import get_llm_client


class SummaryService:
    def __init__(self, client: BaseLLMClient | None = None) -> None:
        self.client = client or get_llm_client(
            provider=settings.summary_llm_provider,
            base_url=settings.summary_llm_base_url,
            api_key=settings.summary_llm_api_key,
            model=settings.summary_llm_model,
            timeout_seconds=settings.summary_llm_timeout_seconds,
            max_retries=settings.summary_llm_max_retries,
            retry_backoff_seconds=settings.summary_llm_retry_backoff_seconds,
        )
        self.temperature = settings.summary_llm_temperature

    def summarize_case(self, messages: list[str]) -> str | None:
        cleaned = [message.strip() for message in messages if message.strip()]
        if not cleaned:
            return None

        if settings.summary_llm_provider == "stub":
            return " | ".join(cleaned[:3])

        system_prompt = (
            "Summarize the support conversation in Russian as a short operational brief. "
            "Use only the provided messages, do not invent facts."
        )
        user_prompt = json.dumps(
            {
                "task": "Create a concise session summary for support routing.",
                "messages": cleaned[-10:],
                "required_json_schema": {
                    "summary": "short string in Russian",
                },
            },
            ensure_ascii=False,
        )
        try:
            raw = self.client.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=self.temperature,
                response_format={"type": "json_object"},
            )
            parsed = json.loads(raw)
            summary = str(parsed.get("summary", "")).strip()
            return summary or None
        except Exception:
            return None
