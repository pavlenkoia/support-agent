from app.services.policy import PolicyService


def test_output_boundary_does_not_replace_natural_customer_text_with_fallback() -> None:
    policy = PolicyService()

    text = "Инструмент для записи указан в сообщении выше."

    assert policy.finalize_simple_customer_text(text, first_reply_in_dialogue=False) == text


def test_output_boundary_adds_standard_greeting_only_to_first_nonempty_reply() -> None:
    policy = PolicyService()

    raw = "Основной номер телефона: +7 (351) 214-30-30."

    assert policy.finalize_simple_customer_text(raw, first_reply_in_dialogue=True) == f"Здравствуйте! {raw}"
    assert policy.finalize_simple_customer_text(raw, first_reply_in_dialogue=False) == raw


def test_output_boundary_does_not_duplicate_model_greeting() -> None:
    policy = PolicyService()

    assert policy.finalize_simple_customer_text("Здравствуйте! Ответ.", first_reply_in_dialogue=True) == "Здравствуйте! Ответ."
