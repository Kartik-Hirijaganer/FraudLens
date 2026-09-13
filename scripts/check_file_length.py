"""Summary: Read-only CLI for the FraudLens 500-physical-line source gate. It
loads config/quality.yaml, applies the temporary shrink-only baseline, prints every
violation, and exits non-zero without modifying source or baseline files.

Key classes:
- (none)

Key functions:
- main: run the configured physical-line and baseline-ratchet checks.

Notes:
- Physical lines intentionally use the same newline-byte definition as wc -l.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from lib.file_length import (
    DEFAULT_QUALITY_CONFIG,
    check_file_lengths,
    load_baseline,
    load_file_length_settings,
)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the configured gate and return one when any source violates it."""
    parser = argparse.ArgumentParser(description="Check the FraudLens 500-line source cap.")
    parser.add_argument("--config", type=Path, default=DEFAULT_QUALITY_CONFIG)
    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    settings = load_file_length_settings(repo_root, args.config)
    violations = check_file_lengths(repo_root, settings, load_baseline(repo_root, settings))
    for violation in violations:
        print(f"{violation.path}: {violation.code}: {violation.message}")
    if violations:
        print(f"\nfile-length-check FAILED: {len(violations)} violation(s)")
        return 1
    print(f"file-length-check OK: all sources satisfy the {settings.max_lines}-line ratchet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
