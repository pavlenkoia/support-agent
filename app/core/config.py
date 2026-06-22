from pydantic_settings import BaseSettings, SettingsConfigDict


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
    knowledge_root: str = "/home/tian/support-agent-kb"
    log_level: str = "INFO"


settings = Settings()
