from __future__ import annotations

from pathlib import Path

import pytest

from scripts.release import (
    APP_SERVICES,
    ReleaseVerificationError,
    verify_profile_artifacts,
    verify_release_evidence,
)


def evidence(*, release_id: str = "a" * 40, started_at: float = 200.0) -> dict[str, dict[str, object]]:
    return {
        service: {
            "release_id": release_id,
            "started_at": started_at,
            "runtime_manifest": "same-manifest",
            "running": True,
        }
        for service in APP_SERVICES
    }


def test_app_plane_is_explicit_and_excludes_database() -> None:
    assert APP_SERVICES == (
        "app",
        "worker",
        "vk-worker",
        "viewer-web",
        "viewer-push-worker",
    )
    assert "db" not in APP_SERVICES


def test_verify_release_evidence_accepts_uniform_running_release() -> None:
    verify_release_evidence(
        evidence(),
        expected_release_id="a" * 40,
        release_started_at=100.0,
        expected_runtime_manifest="same-manifest",
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("release_id", "b" * 40, "release id mismatch"),
        ("started_at", 99.0, "was not recreated"),
        ("runtime_manifest", "old-manifest", "runtime manifest mismatch"),
        ("running", False, "is not running"),
    ],
)
def test_verify_release_evidence_rejects_single_stale_service(
    field: str, value: object, message: str
) -> None:
    actual = evidence()
    actual["vk-worker"][field] = value

    with pytest.raises(ReleaseVerificationError, match=message):
        verify_release_evidence(
            actual,
            expected_release_id="a" * 40,
            release_started_at=100.0,
            expected_runtime_manifest="same-manifest",
        )


def test_verify_release_evidence_rejects_missing_runtime_service() -> None:
    actual = evidence()
    del actual["vk-worker"]

    with pytest.raises(ReleaseVerificationError, match="missing service evidence: vk-worker"):
        verify_release_evidence(
            actual,
            expected_release_id="a" * 40,
            release_started_at=100.0,
            expected_runtime_manifest="same-manifest",
        )


def test_verify_profile_artifacts_accepts_exact_canonical_tree(tmp_path) -> None:
    canonical = tmp_path / "canonical"
    runtime = tmp_path / "runtime"
    for root in (canonical, runtime):
        (root / "kb" / "compiled" / "concepts").mkdir(parents=True)
        (root / "SYSTEM_PROMPT.md").write_text("generic support contract\n", encoding="utf-8")
        (root / "KB_AGENT_PROMPT.md").write_text("generic wiki reader contract\n", encoding="utf-8")
        (root / "kb" / "index.json").write_text('{"pages": []}\n', encoding="utf-8")
        (root / "kb" / "compiled" / "concepts" / "facts.md").write_text("facts\n", encoding="utf-8")

    verify_profile_artifacts(runtime, canonical)


def test_verify_profile_artifacts_rejects_runtime_prompt_drift(tmp_path) -> None:
    canonical = tmp_path / "canonical"
    runtime = tmp_path / "runtime"
    for root in (canonical, runtime):
        (root / "kb").mkdir(parents=True)
        (root / "SYSTEM_PROMPT.md").write_text("generic support contract\n", encoding="utf-8")
        (root / "KB_AGENT_PROMPT.md").write_text("generic wiki reader contract\n", encoding="utf-8")
    (runtime / "SYSTEM_PROMPT.md").write_text("scenario patch\n", encoding="utf-8")

    with pytest.raises(ReleaseVerificationError, match=r"profile artifact mismatch: SYSTEM_PROMPT\.md"):
        verify_profile_artifacts(runtime, canonical)


def test_verify_profile_artifacts_rejects_extra_runtime_file(tmp_path) -> None:
    canonical = tmp_path / "canonical"
    runtime = tmp_path / "runtime"
    for root in (canonical, runtime):
        root.mkdir()
        (root / "SYSTEM_PROMPT.md").write_text("generic support contract\n", encoding="utf-8")
    (runtime / "legacy-prompt.md").write_text("stale scenario\n", encoding="utf-8")

    with pytest.raises(ReleaseVerificationError, match="profile artifact tree mismatch"):
        verify_profile_artifacts(runtime, canonical)
