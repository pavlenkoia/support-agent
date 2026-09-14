from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.knowledge_bundle import build_runtime_knowledge_artifact

URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
SCENARIO_LINE_RE = re.compile(r"^\s*(?:[-*]\s*)?(?:если\s+(?:клиент|пользователь)|when\s+(?:the\s+)?(?:client|user))\b", re.IGNORECASE)
SCENARIO_MARKERS = ("customer-facing", "готовый ответ", "пример ответа", "→")


class ProfileSourceError(ValueError):
    """Raised when a profile source violates the prompt/knowledge boundary."""


def validate_profile_source(source_root: Path) -> None:
    source_root = Path(source_root)
    prompt_path = source_root / "SIMPLE_ANSWER_PROMPT.md"
    if not prompt_path.is_file():
        raise ProfileSourceError("missing profile source artifact: SIMPLE_ANSWER_PROMPT.md")
    prompt = prompt_path.read_text(encoding="utf-8")
    if URL_RE.search(prompt):
        raise ProfileSourceError("SIMPLE_ANSWER_PROMPT.md contains a URL")
    if len(prompt) > 5000:
        raise ProfileSourceError("SIMPLE_ANSWER_PROMPT.md exceeds the generic contract size limit")

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
                    raise ProfileSourceError(f"scenario-style instruction in {relative}:{line_number}")


def build_runtime_profile(source_root: Path, target_root: Path) -> None:
    source_root = Path(source_root)
    target_root = Path(target_root)
    validate_profile_source(source_root)
    if target_root.exists():
        raise ProfileSourceError(f"target runtime profile already exists: {target_root}")
    target_root.mkdir(parents=True)
    shutil.copy2(source_root / "SIMPLE_ANSWER_PROMPT.md", target_root / "SIMPLE_ANSWER_PROMPT.md")
    profile = source_root / "profile.yaml"
    if profile.is_file():
        shutil.copy2(profile, target_root / "profile.yaml")
    (target_root / "kb").mkdir()
    build_runtime_knowledge_artifact(source_root / "kb", target_root / "kb" / "knowledge-base.v1.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a runtime support profile from authored Wiki facts.")
    parser.add_argument("--source", type=Path, default=Path("deploy/profile-source"))
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    build_runtime_profile(args.source, args.target)
    print(args.target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
