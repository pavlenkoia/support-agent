from __future__ import annotations

"""Compatibility CLI for building a reviewed runtime profile.

Business facts and reply scenarios must not be generated from Python code. The reviewed
source tree is compiled by scripts.build_runtime_profile instead.
"""

try:
    from scripts.build_runtime_profile import main
except ModuleNotFoundError:  # direct execution: python scripts/import_kb.py
    from build_runtime_profile import main

if __name__ == "__main__":
    raise SystemExit(main())
