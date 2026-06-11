"""Summary: Shared, read-only git helpers for the scripts that inspect repository
state — scripts/changed_files.py (the changed-files PR gate) and scripts/next_version.py
(the propose-only SemVer bump). One definition of "run a git command and read its lines"
so the consumers stay thin and never drift (rule 5: no duplication). Every call is
read-only (diff, log, rev-parse, describe, ls-files); nothing here writes refs or history.

Key classes:
- (none)

Key functions:
- run_git: run a git command from the repo root; return stdout ('' on non-zero exit).
- run_lines: run a git command via an injectable runner; return its non-empty lines.
- ref_exists: return True when a ref resolves to a commit (via an injectable runner).

Notes:
- run_git never raises on a failed git call; it returns '' so callers degrade gracefully.
- The GitRunner indirection lets callers inject a fake runner so logic is unit-tested
  without a real repository.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

GitRunner = Callable[[list[str]], str]

# scripts/lib/gitio.py -> repo root is two levels up.
_REPO_ROOT = Path(__file__).resolve().parents[2]


def run_git(args: list[str]) -> str:
    """Run a git command from the repo root and return stdout ('' on failure)."""
    result = subprocess.run(
        ["git", *args],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else ""


def run_lines(run: GitRunner, args: list[str]) -> list[str]:
    """Run a git command and return its non-empty, stripped output lines."""
    return [line for line in run(args).splitlines() if line.strip()]


def ref_exists(ref: str, run: GitRunner) -> bool:
    """Return True when ref resolves to a commit."""
    return bool(run_lines(run, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"]))
