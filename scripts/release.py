"""Single, evidence-gated production release command for support-agent."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import urlopen

APP_SERVICES = (
    "app",
    "worker",
    "vk-worker",
    "viewer-web",
    "viewer-push-worker",
)
RUNTIME_CODE_SERVICES = {"app", "worker", "vk-worker"}
OCI_REVISION_LABEL = "org.opencontainers.image.revision"
ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = Path(os.environ.get("SUPPORT_AGENT_RUNTIME_ROOT_HOST", "/home/tian/support-agent-runtime"))


class ReleaseVerificationError(RuntimeError):
    """Raised when production evidence does not prove a complete release."""


def verify_release_evidence(
    evidence: Mapping[str, Mapping[str, object]],
    *,
    expected_release_id: str,
    release_started_at: float,
    expected_runtime_manifest: str,
) -> None:
    """Reject any app-plane service that is missing, stale, or not running."""
    for service in APP_SERVICES:
        item = evidence.get(service)
        if item is None:
            raise ReleaseVerificationError(f"missing service evidence: {service}")
        if item.get("release_id") != expected_release_id:
            raise ReleaseVerificationError(f"{service}: release id mismatch")
        started_at = item.get("started_at")
        if not isinstance(started_at, (int, float)) or started_at < release_started_at:
            raise ReleaseVerificationError(f"{service}: was not recreated for this release")
        if service in RUNTIME_CODE_SERVICES and (
            item.get("runtime_manifest") != expected_runtime_manifest
        ):
            raise ReleaseVerificationError(f"{service}: runtime manifest mismatch")
        if item.get("running") is not True:
            raise ReleaseVerificationError(f"{service}: is not running")


def command(
    args: Sequence[str], *, env: Mapping[str, str] | None = None, capture: bool = False
) -> str:
    completed = subprocess.run(
        list(args), cwd=ROOT, env=dict(env) if env else None, text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        check=False,
    )
    if completed.returncode:
        detail = completed.stdout.strip() if capture and completed.stdout else "command failed"
        raise RuntimeError(f"{' '.join(args)}: {detail}")
    return completed.stdout.strip() if capture and completed.stdout else ""


def compose(args: Sequence[str], *, env: Mapping[str, str], capture: bool = False) -> str:
    return command(("docker", "compose", *args), env=env, capture=capture)


def git_output(*args: str) -> str:
    return command(("git", *args), capture=True)


def ensure_clean_checkout() -> str:
    if git_output("status", "--porcelain"):
        raise RuntimeError("release checkout is not clean; commit or stash all changes first")
    return git_output("rev-parse", "HEAD")


def source_manifest(root: Path) -> str:
    """Digest the complete Python runtime tree, with paths included."""
    lines: list[str] = []
    for file_path in sorted((root / "app").rglob("*.py")):
        relative = file_path.relative_to(root).as_posix()
        digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {relative}")
    if not lines:
        raise RuntimeError("runtime source manifest is empty")
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def parse_started_at(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def service_container_id(service: str, env: Mapping[str, str]) -> str:
    value = compose(("ps", "-q", service), env=env, capture=True)
    if not value:
        raise ReleaseVerificationError(f"missing container for {service}")
    return value.splitlines()[-1]


def container_runtime_manifest(container_id: str, env: Mapping[str, str]) -> str:
    output = command(
        (
            "docker", "exec", container_id, "sh", "-ec",
            "cd /app && find app -type f -name '*.py' -print0 | sort -z | xargs -0 sha256sum",
        ),
        env=env,
        capture=True,
    )
    return hashlib.sha256(output.encode()).hexdigest()


def collect_evidence(env: Mapping[str, str]) -> dict[str, dict[str, object]]:
    evidence: dict[str, dict[str, object]] = {}
    for service in APP_SERVICES:
        container_id = service_container_id(service, env)
        inspect = json.loads(command(("docker", "inspect", container_id), env=env, capture=True))[0]
        image_id = inspect["Image"]
        image = json.loads(command(("docker", "image", "inspect", image_id), env=env, capture=True))[0]
        labels = image.get("Config", {}).get("Labels") or {}
        item: dict[str, object] = {
            "container_id": container_id,
            "image_id": image_id,
            "release_id": labels.get(OCI_REVISION_LABEL),
            "started_at": parse_started_at(inspect["State"]["StartedAt"]),
            "running": inspect["State"].get("Running") is True,
        }
        if service in RUNTIME_CODE_SERVICES:
            item["runtime_manifest"] = container_runtime_manifest(container_id, env)
        evidence[service] = item
    return evidence


def verify_http_health() -> dict[str, object]:
    deadline = time.monotonic() + 45
    error: str | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen("http://127.0.0.1:8000/health", timeout=5) as response:
                payload = json.loads(response.read())
            if payload.get("status") == "ok":
                return payload
            error = f"unexpected health payload: {payload!r}"
        except Exception as exc:  # retry while Compose starts app
            error = f"{type(exc).__name__}: {exc}"
        time.sleep(2)
    raise ReleaseVerificationError(f"backend health did not become ready: {error}")


def verify_worker_liveness(env: Mapping[str, str]) -> dict[str, bool]:
    required = {"worker": "telegram polling tick", "vk-worker": "vk polling tick"}
    result: dict[str, bool] = {}
    deadline = time.monotonic() + 70
    while time.monotonic() < deadline:
        result = {
            service: marker in compose(("logs", "--since", "2m", service), env=env, capture=True)
            for service, marker in required.items()
        }
        if all(result.values()):
            return result
        time.sleep(2)
    missing = ", ".join(service for service, ok in result.items() if not ok)
    raise ReleaseVerificationError(f"worker liveness marker missing: {missing}")


def verify_grounded_fallback(env: Mapping[str, str]) -> dict[str, str]:
    probe = r'''import json
from app.services import direct_llm as module
from app.services.direct_llm import DirectLLMService
class BrokenClient:
    def generate(self, **kwargs):
        raise RuntimeError("IncompleteRead")
module.settings.direct_llm_provider = "mistral"
service = DirectLLMService(client=BrokenClient())
service._fallback_answer_from_grounding = lambda *args, **kwargs: None
result = service.respond("Можно ли в тандеме при весе 120 кг?", {
    "kb_status": "found", "grounding_status": "ready",
    "grounded_facts": ["Максимальный вес для тандем-прыжка — до 85 кг."],
    "answer_basis": "При весе 120 кг тандем-прыжок невозможен.",
})
assert result["route"] == "answer", result
assert "до 85 кг" in result["response_text"], result
assert result["reason"] == "prompt_runtime_grounded_fallback:RuntimeError", result
print(json.dumps({"route": result["route"], "reason": result["reason"]}, ensure_ascii=False))'''
    result: dict[str, str] = {}
    for service in RUNTIME_CODE_SERVICES:
        output = compose(("exec", "-T", service, "uv", "run", "--no-dev", "python", "-c", probe), env=env, capture=True)
        result[service] = output.splitlines()[-1]
    return result


def receipt_path(release_id: str, started_at: float) -> Path:
    directory = RUNTIME_ROOT / "releases"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    stamp = datetime.fromtimestamp(started_at, UTC).strftime("%Y%m%dT%H%M%SZ")
    return directory / f"{stamp}-{release_id[:12]}.json"


def write_receipt(path: Path, payload: Mapping[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    os.chmod(path, 0o600)


def run_release(*, release_id: str, expected_manifest: str, build: bool) -> Path:
    started_at = time.time()
    env = {**os.environ, "SUPPORT_AGENT_RELEASE_ID": release_id}
    receipt: dict[str, object] = {
        "release_id": release_id,
        "release_started_at": started_at,
        "expected_runtime_manifest": expected_manifest,
        "services": list(APP_SERVICES),
        "db_recreated": False,
        "status": "failed",
    }
    path = receipt_path(release_id, started_at)
    try:
        compose(("config", "-q"), env=env)
        if build:
            compose(("build", *APP_SERVICES), env=env)
        compose(("up", "-d", "--force-recreate", "--no-deps", *APP_SERVICES), env=env)
        evidence = collect_evidence(env)
        verify_release_evidence(
            evidence,
            expected_release_id=release_id,
            release_started_at=started_at,
            expected_runtime_manifest=expected_manifest,
        )
        receipt["evidence"] = evidence
        receipt["health"] = verify_http_health()
        receipt["worker_liveness"] = verify_worker_liveness(env)
        receipt["grounded_fallback_probe"] = verify_grounded_fallback(env)
        receipt["status"] = "success"
    except Exception as exc:
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        write_receipt(path, receipt)
        raise
    write_receipt(path, receipt)
    return path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rollback-receipt", type=Path, help="successful JSON receipt to roll back to")
    args = parser.parse_args(argv)
    try:
        if args.rollback_receipt:
            prior = json.loads(args.rollback_receipt.read_text())
            if prior.get("status") != "success":
                raise RuntimeError("rollback receipt is not a successful release")
            release_id = str(prior["release_id"])
            manifest = str(prior["expected_runtime_manifest"])
            path = run_release(release_id=release_id, expected_manifest=manifest, build=False)
        else:
            release_id = ensure_clean_checkout()
            path = run_release(release_id=release_id, expected_manifest=source_manifest(ROOT), build=True)
    except Exception as exc:
        print(f"RELEASE FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"RELEASE SUCCESS: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
