from types import SimpleNamespace

from scripts.classify_legacy_vk_events import classify


def test_legacy_vk_timeout_requires_operator_review_without_replay() -> None:
    event = SimpleNamespace(status="waiting_human", error_text="received_timeout_without_finalization")

    assert classify(event) == "requires_operator_review"


def test_unknown_nonterminal_event_is_not_silently_classified_retryable() -> None:
    event = SimpleNamespace(status="received", error_text=None)

    assert classify(event) == "unknown_nonterminal"
