#!/usr/bin/env python3
"""
scripts/validate_config.py
==========================
Continuous Integration & Local Pre-Commit Config Drift Validator.

Verifies:
1. Every configuration key in `config.py:DEFAULT_CONFIG` is represented in `config.example.json`.
2. Every key in `config.example.json` is a valid member of `config.py:DEFAULT_CONFIG` (no orphaned keys).
3. Every key in `config.py:DEFAULT_CONFIG` is documented in `README.md`.

Exit Codes:
- 0: Configuration, example JSON, and documentation are in 100% harmony.
- 1: Drift detected (missing or orphaned keys).
"""

import sys
import json
from pathlib import Path

# Resolve repository root
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    from config import DEFAULT_CONFIG
except ImportError as err:
    print(f"❌ [CONFIG DRIFT] Failed to import config.py: {err}", file=sys.stderr)
    sys.exit(1)

EXAMPLE_JSON_PATH = REPO_ROOT / "config.example.json"
README_PATH = REPO_ROOT / "README.md"

def main() -> int:
    errors = []

    if not EXAMPLE_JSON_PATH.exists():
        print(f"❌ [CONFIG DRIFT] Missing file: {EXAMPLE_JSON_PATH}", file=sys.stderr)
        return 1

    if not README_PATH.exists():
        print(f"❌ [CONFIG DRIFT] Missing file: {README_PATH}", file=sys.stderr)
        return 1

    try:
        example_cfg = json.loads(EXAMPLE_JSON_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ [CONFIG DRIFT] Failed to parse config.example.json: {e}", file=sys.stderr)
        return 1

    readme_text = README_PATH.read_text(encoding="utf-8")

    default_keys = set(DEFAULT_CONFIG.keys())
    example_keys = set(example_cfg.keys())

    # 1. Missing in example.json
    missing_in_example = default_keys - example_keys
    if missing_in_example:
        errors.append(f"Keys defined in DEFAULT_CONFIG but missing in config.example.json: {sorted(missing_in_example)}")

    # 2. Orphaned in example.json
    orphaned_in_example = example_keys - default_keys
    if orphaned_in_example:
        errors.append(f"Keys present in config.example.json but not in DEFAULT_CONFIG: {sorted(orphaned_in_example)}")

    # 3. Missing in README.md
    missing_in_readme = [k for k in sorted(default_keys) if f"`{k}`" not in readme_text and k not in readme_text]
    if missing_in_readme:
        errors.append(f"Keys defined in DEFAULT_CONFIG but not documented in README.md: {missing_in_readme}")

    if errors:
        print("=" * 70, file=sys.stderr)
        print("❌ CONFIG DRIFT VALIDATION FAILED:", file=sys.stderr)
        for err in errors:
            print(f"  • {err}", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        return 1

    print(f"✅ [CONFIG DRIFT] Success: All {len(default_keys)} keys in DEFAULT_CONFIG match config.example.json and are documented in README.md.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
