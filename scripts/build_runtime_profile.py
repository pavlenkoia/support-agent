from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.knowledge_bundle import (
    compile_legacy_bundle,
    validate_compiled_bundle,
)

URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
SCENARIO_LINE_RE = re.compile(r"^\s*(?:[-*]\s*)?(?:если\s+(?:клиент|пользователь)|when\s+(?:the\s+)?(?:client|user))\b", re.IGNORECASE)
SCENARIO_MARKERS = ("customer-facing", "готовый ответ", "пример ответа", "→")


class ProfileSourceError(ValueError):
    """Raised when a profile source violates the prompt/knowledge boundary."""


def validate_profile_source(source_root: Path) -> None:
    source_root = Path(source_root)
    for name in ("SYSTEM_PROMPT.md", "KB_AGENT_PROMPT.md"):
        path = source_root / name
        if not path.is_file():
            raise ProfileSourceError(f"missing profile source artifact: {name}")
        text = path.read_text(encoding="utf-8")
        if URL_RE.search(text):
            raise ProfileSourceError(f"{name} contains a URL")
        if len(text) > 5000:
            raise ProfileSourceError(f"{name} exceeds the generic contract size limit")

    kb_root = source_root / "kb"
    for directory in ("concepts", "entities", "comparisons", "queries"):
        page_root = kb_root / directory
        if not page_root.is_dir():
            continue
        for path in sorted(page_root.rglob("*.md")):
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                normalized = line.casefold()
                if SCENARIO_LINE_RE.search(line) or any(marker in normalized for marker in SCENARIO_MARKERS):
                    relative = path.relative_to(source_root).as_posix()
                    raise ProfileSourceError(
                        f"scenario-style instruction in {relative}:{line_number}"
                    )


def build_runtime_profile(source_root: Path, target_root: Path) -> None:
    source_root = Path(source_root)
    target_root = Path(target_root)
    validate_profile_source(source_root)
    if target_root.exists():
        raise ProfileSourceError(f"target runtime profile already exists: {target_root}")
    target_root.mkdir(parents=True)
    for name in ("SYSTEM_PROMPT.md", "KB_AGENT_PROMPT.md", "profile.yaml"):
        source = source_root / name
        if source.is_file():
            shutil.copy2(source, target_root / name)
    compile_legacy_bundle(source_root / "kb", target_root / "kb")
    report = validate_compiled_bundle(target_root / "kb")
    if not report["valid"]:
        raise ProfileSourceError("compiled runtime profile is invalid: " + "; ".join(report["errors"]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an immutable runtime support profile from reviewed prompt and Wiki sources.")
    parser.add_argument("--source", type=Path, default=Path("deploy/profile-source"))
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    build_runtime_profile(args.source, args.target)
    print(args.target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
