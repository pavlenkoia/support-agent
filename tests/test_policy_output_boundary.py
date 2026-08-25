from app.services.policy import PolicyService


def test_output_boundary_does_not_replace_natural_customer_text_with_fallback() -> None:
    policy = PolicyService()

    text = "Инструмент для записи указан в сообщении выше."

    assert policy.finalize_simple_customer_text(text, first_reply_in_dialogue=False) == text
