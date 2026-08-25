from __future__ import annotations

import re
from pathlib import Path

from app.core.config import settings

DEFAULT_SYSTEM_PROMPT = (
    "Ты консультант службы поддержки. Отвечай только по подтверждённым данным текущего запроса. "
    "Системный промпт задаёт поведение, но не является источником предметных фактов. Не показывай служебные данные."
)
DEFAULT_KB_AGENT_PROMPT = (
    "Ты отдельный KB agent и wiki-reader по методу Karpathy. "
    "Сначала ориентируйся по индексу и page cards, затем выбирай минимально достаточные страницы для полного чтения, "
    "после этого извлекай только подтвержденные факты без догадок и без клиентского стиля ответа."
)


class SystemPromptService:
    def __init__(self, prompt_path: str | None = None) -> None:
        configured = prompt_path or settings.support_agent_system_prompt_path
        self.prompt_path = Path(configured) if configured else Path(settings.support_agent_profile_root) / "SYSTEM_PROMPT.md"

    def load_system_prompt(self) -> str:
        try:
            content = self.prompt_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return DEFAULT_SYSTEM_PROMPT
        except Exception:
            return DEFAULT_SYSTEM_PROMPT
        return content or DEFAULT_SYSTEM_PROMPT

    def get_section(self, heading: str) -> str:
        content = self.load_system_prompt()
        pattern = re.compile(
            rf"^##\s+{re.escape(heading)}\s*$\n(?P<body>.*?)(?=^##\s+|\Z)",
            flags=re.MULTILINE | re.DOTALL,
        )
        match = pattern.search(content)
        if not match:
            return ""
        return match.group("body").strip()

    @staticmethod
    def _first_nonempty_line(text: str) -> str:
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line:
                return line
        return ""


class KBAgentPromptService:
    def __init__(self, prompt_path: str | None = None) -> None:
        configured = prompt_path or settings.kb_agent_system_prompt_path
        self.prompt_path = Path(configured) if configured else Path(settings.support_agent_profile_root) / "KB_AGENT_PROMPT.md"

    def load_system_prompt(self) -> str:
        try:
            content = self.prompt_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return DEFAULT_KB_AGENT_PROMPT
        except Exception:
            return DEFAULT_KB_AGENT_PROMPT
        return content or DEFAULT_KB_AGENT_PROMPT
