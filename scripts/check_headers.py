"""Summary: CI-blocking validator for the top-of-file SUMMARY headers (rule 2). It
walks every source file subject to the rule and reports any header that is missing,
has the four sections out of order, or whose Key classes / Key functions inventory
does not match the file's actual public symbols. Read-only — it never edits files;
`make docs` (scripts/update_docs.py) is the writer that fixes inventory drift.

Key classes:
- (none)

Key functions:
- main: validate all source headers and return a process exit code.

Notes:
- The validation logic lives in scripts/lib/headers.py so the writer and the
  validator share one definition of the contract (no duplication).
"""

from __future__ import annotations

from pathlib import Path

from lib.headers import iter_source_files, validate_header


def main() -> int:
    """Validate every source file's SUMMARY header; return 1 if any file is invalid."""
    repo_root = Path(__file__).resolve().parents[1]
    failing = 0
    for path in iter_source_files(repo_root):
        violations = validate_header(path)
        if violations:
            failing += 1
            rel = path.relative_to(repo_root)
            for violation in violations:
                print(f"{rel}: {violation}")
    if failing:
        print(f"\nheader-check FAILED: {failing} file(s) with header violations")
        return 1
    print("header-check OK: all source files carry a valid SUMMARY header")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
