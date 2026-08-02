from pathlib import Path

from tools.prodlike_parity import sync_llm_settings, verify_llm_parity


def test_sync_llm_settings_makes_test_env_match_production_without_replacing_test_database(tmp_path: Path) -> None:
    production = tmp_path / "production.env"
    test = tmp_path / "test.env"
    production.write_text(
        "DATABASE_URL=postgresql://production\n"
        "DIRECT_LLM_PROVIDER=mistral\n"
        "DIRECT_LLM_MODEL=mistral-small-latest\n"
        "DIRECT_LLM_API_KEYS=prod-direct-key\n"
        "KB_AGENT_PROVIDER=mistral\n"
        "KB_AGENT_MODEL=mistral-medium-latest\n"
        "KB_AGENT_API_KEYS=prod-kb-key\n"
        "SUMMARY_LLM_PROVIDER=mistral\n"
        "SUMMARY_LLM_MODEL=mistral-small-latest\n"
        "SUMMARY_LLM_API_KEYS=prod-summary-key\n",
        encoding="utf-8",
    )
    test.write_text(
        "DATABASE_URL=postgresql://test\n"
        "DIRECT_LLM_PROVIDER=openai_compatible\n"
        "DIRECT_LLM_MODEL=other\n"
        "KB_AGENT_PROVIDER=openai_compatible\n"
        "KB_AGENT_MODEL=retired-test-model\n"
        "KB_AGENT_API_KEYS=old-key\n"
        "KB_AGENT_SYSTEM_PROMPT_PATH=/old/prompt.md\n",
        encoding="utf-8",
    )

    sync_llm_settings(production, test)

    assert verify_llm_parity(production, test) == []
    rendered = test.read_text(encoding="utf-8")
    assert "DATABASE_URL=postgresql://test" in rendered
    assert "KB_AGENT_SYSTEM_PROMPT_PATH" not in rendered
    assert "prod-kb-key" in rendered
