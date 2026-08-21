from app.integrations.vk.client import VKAPIClient


def test_vk_rest_client_uses_current_vk_ru_api_host() -> None:
    assert VKAPIClient.api_base_url == "https://api.vk.ru/method/"
