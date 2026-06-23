from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_csv_set(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in value.split(',') if item.strip()}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="deploy/env/app.env", extra="ignore")

    app_name: str = "support-agent"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    database_url: str = "postgresql+psycopg://support_agent:support_agent@db:5432/support_agent"
    direct_llm_provider: str = "stub"
    direct_llm_model: str = "stub"
    hermes_backend_enabled: bool = False
    hermes_backend_mode: str = "stub"
    knowledge_backend: str = "filesystem"
    knowledge_root: str = "/data/support-agent-kb"
    telegram_bot_token: str | None = None
    telegram_allowed_chats: str = ""
    telegram_poll_timeout_seconds: int = 30
    telegram_poll_interval_seconds: int = 3
    telegram_poll_offset_file: str = "/app/logs/telegram-update-offset.txt"
    log_level: str = "INFO"


settings = Settings()
