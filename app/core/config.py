from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_csv_set(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in value.split(',') if item.strip()}


def parse_csv_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(',') if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="deploy/env/app.env", extra="ignore")

    app_name: str = "support-agent"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    database_url: str = "postgresql+psycopg://support_agent:support_agent@db:5432/support_agent"

    direct_llm_provider: str = "stub"
    direct_llm_base_url: str | None = None
    direct_llm_api_key: str | None = None
    direct_llm_api_keys: str = ""
    direct_llm_model: str = "stub"
    direct_llm_temperature: float = 0.0
    direct_llm_timeout_seconds: int = 45
    direct_llm_max_retries: int = 3
    direct_llm_retry_backoff_seconds: float = 0.75
    direct_llm_retry_deadline_seconds: float = 45.0
    openai_compatible_drop_params: bool = False

    kb_agent_provider: str | None = None
    kb_agent_base_url: str | None = None
    kb_agent_api_key: str | None = None
    kb_agent_api_keys: str = ""
    kb_agent_model: str | None = None
    kb_agent_temperature: float = 0.0
    kb_agent_timeout_seconds: int | None = None
    kb_agent_max_retries: int | None = None
    kb_agent_retry_backoff_seconds: float | None = None
    kb_agent_retry_deadline_seconds: float | None = None
    kb_agent_skip_coverage_review: bool = False
    kb_agent_deterministic_navigation: bool = False
    kb_agent_minimal_extraction_schema: bool = False
    kb_agent_deferred_retry_delay_seconds: int = 5

    summary_llm_provider: str = "stub"
    summary_llm_base_url: str | None = None
    summary_llm_api_key: str | None = None
    summary_llm_api_keys: str = ""
    summary_llm_model: str = "stub"
    summary_llm_temperature: float = 0.0
    summary_llm_timeout_seconds: int = 45
    summary_llm_max_retries: int = 2
    summary_llm_retry_backoff_seconds: float = 1.0
    summary_llm_retry_deadline_seconds: float = 45.0

    hermes_backend_enabled: bool = False
    hermes_backend_mode: str = "stub"
    support_agent_profile_root: str = "/data/profile"
    support_agent_system_prompt_path: str | None = None
    kb_agent_system_prompt_path: str | None = None
    knowledge_backend: str = "filesystem"
    knowledge_root: str | None = None
    answer_engine_mode: str = "legacy"
    simple_answer_max_corpus_chars: int = 50_000
    telegram_bot_token: str | None = None
    telegram_allowed_chats: str = ""
    telegram_poll_timeout_seconds: int = 30
    telegram_poll_interval_seconds: int = 3
    telegram_poll_offset_file: str = "/app/logs/telegram-update-offset.txt"
    inbound_coalesce_quiet_seconds: int = 5
    inbound_coalesce_max_wait_seconds: int = 15

    vk_enabled: bool = False
    vk_group_id: str | None = None
    vk_access_token: str | None = None
    vk_api_version: str = "5.199"
    vk_longpoll_wait_seconds: int = 5
    vk_longpoll_mode: int | None = None
    vk_longpoll_version: int | None = None
    vk_poll_interval_seconds: int = 3
    vk_poll_state_file: str = "/app/logs/vk-longpoll-state.json"
    vk_request_timeout_seconds: int = 30
    vk_max_retries: int = 3
    vk_retry_backoff_seconds: float = 1.0
    vk_override_silence_seconds: int = 3600
    vk_received_event_timeout_seconds: int = 90
    vk_generation_lease_seconds: int = 600

    viewer_timezone: str = "Asia/Yekaterinburg"
    viewer_auth_enabled: bool = False
    viewer_auth_key: str | None = None
    viewer_auth_cookie_name: str = "viewer_auth"
    viewer_auth_session_days: int = 365
    viewer_auth_cookie_secure: bool = False
    viewer_push_enabled: bool = False
    viewer_push_vapid_public_key: str | None = None
    viewer_push_vapid_private_key: str | None = None
    viewer_push_vapid_subject: str | None = None
    viewer_push_poll_interval_seconds: int = 5
    viewer_push_ttl_seconds: int = 86400
    viewer_push_urgency: str = "high"
    viewer_push_processing_lease_seconds: int = 90

    log_level: str = "INFO"

    @property
    def direct_llm_api_key_list(self) -> list[str]:
        keys = parse_csv_list(self.direct_llm_api_keys)
        if self.direct_llm_api_key and self.direct_llm_api_key not in keys:
            return [self.direct_llm_api_key, *keys]
        return keys

    @property
    def kb_agent_api_key_list(self) -> list[str]:
        keys = parse_csv_list(self.kb_agent_api_keys)
        if self.kb_agent_api_key and self.kb_agent_api_key not in keys:
            return [self.kb_agent_api_key, *keys]
        return keys

    @property
    def summary_llm_api_key_list(self) -> list[str]:
        keys = parse_csv_list(self.summary_llm_api_keys)
        if self.summary_llm_api_key and self.summary_llm_api_key not in keys:
            return [self.summary_llm_api_key, *keys]
        return keys

    def model_post_init(self, __context) -> None:
        if not self.support_agent_system_prompt_path:
            self.support_agent_system_prompt_path = str(Path(self.support_agent_profile_root) / "SYSTEM_PROMPT.md")

        if not self.kb_agent_system_prompt_path:
            self.kb_agent_system_prompt_path = str(Path(self.support_agent_profile_root) / "KB_AGENT_PROMPT.md")

        if not self.knowledge_root:
            self.knowledge_root = str(Path(self.support_agent_profile_root) / "kb")

        if not self.kb_agent_provider:
            self.kb_agent_provider = self.direct_llm_provider
        if not self.kb_agent_base_url:
            self.kb_agent_base_url = self.direct_llm_base_url
        if not self.direct_llm_api_key and self.direct_llm_api_key_list:
            self.direct_llm_api_key = self.direct_llm_api_key_list[0]
        if not self.kb_agent_api_keys:
            self.kb_agent_api_keys = self.direct_llm_api_keys
        if not self.kb_agent_api_key:
            self.kb_agent_api_key = self.direct_llm_api_key
        if not self.kb_agent_model:
            self.kb_agent_model = self.direct_llm_model
        if self.kb_agent_timeout_seconds is None:
            self.kb_agent_timeout_seconds = self.direct_llm_timeout_seconds
        if self.kb_agent_max_retries is None:
            self.kb_agent_max_retries = self.direct_llm_max_retries
        if self.kb_agent_retry_backoff_seconds is None:
            self.kb_agent_retry_backoff_seconds = self.direct_llm_retry_backoff_seconds
        if self.kb_agent_retry_deadline_seconds is None:
            self.kb_agent_retry_deadline_seconds = self.direct_llm_retry_deadline_seconds

        if not self.direct_llm_base_url and self.direct_llm_provider == "mistral":
            self.direct_llm_base_url = "https://api.mistral.ai/v1"

        if not self.kb_agent_base_url and self.kb_agent_provider == "mistral":
            self.kb_agent_base_url = "https://api.mistral.ai/v1"

        if not self.summary_llm_api_key and self.summary_llm_api_key_list:
            self.summary_llm_api_key = self.summary_llm_api_key_list[0]

        if not self.summary_llm_base_url and self.summary_llm_provider == "mistral":
            self.summary_llm_base_url = "https://api.mistral.ai/v1"


settings = Settings()
