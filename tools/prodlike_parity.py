from __future__ import annotations

import argparse
from pathlib import Path

LLM_PREFIXES = ("DIRECT_LLM_", "SUMMARY_LLM_")
# This transport compatibility flag applies to the single answer call and is
# part of parity between the production and prod-like environments.
LLM_PARITY_KEYS = {"OPENAI_COMPATIBLE_DROP_PARAMS"}


def is_llm_parity_key(key: str) -> bool:
    return key.startswith(LLM_PREFIXES) or key in LLM_PARITY_KEYS


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def verify_llm_parity(production_path: Path, test_path: Path) -> list[str]:
    production = parse_env_file(production_path)
    test = parse_env_file(test_path)
    production_llm = {key: value for key, value in production.items() if is_llm_parity_key(key)}
    test_llm = {key: value for key, value in test.items() if is_llm_parity_key(key)}
    return sorted(
        key
        for key in set(production_llm) | set(test_llm)
        if production_llm.get(key) != test_llm.get(key)
    )


def sync_llm_settings(production_path: Path, test_path: Path) -> None:
    production = parse_env_file(production_path)
    test = parse_env_file(test_path)
    for key in list(test):
        if is_llm_parity_key(key):
            del test[key]
    test.update({key: value for key, value in production.items() if is_llm_parity_key(key)})
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text(
        "\n".join(f"{key}={value}" for key, value in sorted(test.items())) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Synchronize or verify non-secret production-parity LLM settings.")
    parser.add_argument("--sync", action="store_true", help="copy all LLM-role settings from production to the prod-like test env")
    parser.add_argument("--production-env", type=Path, default=Path("/home/tian/support-agent-runtime/app.env"))
    parser.add_argument("--test-env", type=Path, default=Path("/home/tian/support-agent-runtime-prodlike-test/app.env"))
    args = parser.parse_args()
    if args.sync:
        sync_llm_settings(args.production_env, args.test_env)
    mismatches = verify_llm_parity(args.production_env, args.test_env)
    if mismatches:
        print("prod-like LLM parity mismatch: " + ", ".join(mismatches))
        return 1
    print("prod-like LLM parity: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
