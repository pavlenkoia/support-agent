from app.core.config import Settings


def test_settings_parse_direct_api_keys_and_default_primary_key() -> None:
    settings = Settings.model_validate(
        {
            "direct_llm_provider": "mistral",
            "direct_llm_model": "mistral-small",
            "direct_llm_api_key": None,
            "direct_llm_api_keys": "primary-key, secondary-key ",
        }
    )

    assert settings.direct_llm_api_key == "primary-key"
    assert settings.direct_llm_api_key_list == ["primary-key", "secondary-key"]


def test_settings_kb_agent_inherits_direct_api_keys_when_not_overridden() -> None:
    settings = Settings.model_validate(
        {
            "direct_llm_provider": "mistral",
            "direct_llm_model": "mistral-small",
            "direct_llm_api_keys": "primary-key,secondary-key",
            "kb_agent_api_key": None,
            "kb_agent_api_keys": "",
        }
    )

    assert settings.kb_agent_api_key == "primary-key"
    assert settings.kb_agent_api_key_list == ["primary-key", "secondary-key"]
