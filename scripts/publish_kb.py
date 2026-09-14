#!/usr/bin/env python3
"""Build and atomically publish the curated support-agent runtime KB.

The editor (including support-governor) changes only the canonical fact registry
under deploy/profile-source/kb/. This command is the sole path that moves a
validated generated artifact into the read-only profile mounted by executors.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.knowledge_bundle import build_runtime_knowledge_artifact


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and atomically publish support-agent KB")
    parser.add_argument("--source-kb", type=Path, default=Path("deploy/profile-source/kb"))
    parser.add_argument("--runtime-kb", type=Path, default=Path("deploy/profile/kb"))
    parser.add_argument("--receipt-dir", type=Path, default=Path("deploy/governor-receipts"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    source = args.source_kb / "runtime-facts.v1.json"
    live = args.runtime_kb / "knowledge-base.v1.json"
    if not source.is_file():
        raise SystemExit(f"missing canonical fact registry: {source}")
    if not args.runtime_kb.is_dir():
        raise SystemExit(f"missing runtime KB directory: {args.runtime_kb}")

    with tempfile.TemporaryDirectory(prefix="support-kb-publish-") as tmp:
        candidate = Path(tmp) / "knowledge-base.v1.json"
        # The builder validates the exact source schema before writing a candidate.
        build_runtime_knowledge_artifact(args.source_kb, candidate)
        candidate_hash = sha256(candidate)
        old_hash = sha256(live) if live.is_file() else None
        receipt = {
            "published_at": datetime.now(timezone.utc).isoformat(),
            "source": str(source),
            "runtime_target": str(live),
            "candidate_sha256": candidate_hash,
            "previous_sha256": old_hash,
            "changed": candidate_hash != old_hash,
            "dry_run": args.dry_run,
        }
        if not args.dry_run and candidate_hash != old_hash:
            # os.replace is atomic on the same filesystem. Readers see old or new,
            # never a partial JSON file.
            staged = args.runtime_kb / ".knowledge-base.v1.json.next"
            staged.write_bytes(candidate.read_bytes())
            os.replace(staged, live)
        args.receipt_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        receipt_path = args.receipt_dir / f"kb-publish-{stamp}.json"
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({**receipt, "receipt": str(receipt_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
