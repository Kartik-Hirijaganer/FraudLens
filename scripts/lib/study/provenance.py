"""Summary: Source-revision provenance shared by every artifact that records what produced it.

Key classes:
- (none)

Key functions:
- git_commit: require a clean worktree and return its immutable HEAD revision.

Notes:
- A DIRTY worktree fails rather than recording a commit the artifact was not actually produced
  from: an evidence file that names an innocent SHA is worse than one that names none.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

GIT_SHA_PATTERN = r"^[0-9a-f]{40}$"


def _command_output(command: tuple[str, ...], *, cwd: Path) -> str:
    """Run one read-only local command and return its trimmed output."""
    return subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def git_commit(repo_root: Path) -> str:
    """Require a clean source tree and return its immutable HEAD revision."""
    if _command_output(("git", "status", "--porcelain"), cwd=repo_root):
        raise ValueError("recorded provenance requires a clean committed worktree")
    commit = _command_output(("git", "rev-parse", "HEAD"), cwd=repo_root)
    if re.fullmatch(GIT_SHA_PATTERN, commit) is None:
        raise ValueError("unable to resolve an immutable Git commit")
    return commit
