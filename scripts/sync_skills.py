"""Summary: CLI for validating and mirroring FraudLens project skills. Writer mode
copies the canonical .claude/skills tree into .agents/skills; --check reports exact
tree drift without modifying either location.

Key classes:
- (none)

Key functions:
- main: validate project skills, then synchronize or check the Codex mirror.

Notes:
- `make docs` uses writer mode and `make docs-check` uses read-only check mode.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from lib.skills import compare_skill_trees, sync_skill_trees, validate_skills

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPO_ROOT / ".claude" / "skills"
DEFAULT_MIRROR = REPO_ROOT / ".agents" / "skills"


def main(argv: Sequence[str] | None = None) -> int:
    """Validate skills and synchronize or check their generated mirror."""
    parser = argparse.ArgumentParser(description="Synchronize repository-scoped agent skills.")
    parser.add_argument("--check", action="store_true", help="report drift without writing")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--mirror", type=Path, default=DEFAULT_MIRROR)
    args = parser.parse_args(argv)

    if args.check:
        differences = compare_skill_trees(args.source, args.mirror)
        if differences:
            print("skills-check FAILED — stale (run `make docs`):")
            for difference in differences:
                print(f"  {difference.path}: {difference.code}: {difference.message}")
            return 1
        count = len(validate_skills(args.source))
        print(f"skills-check OK: {count} project skill(s) are valid and byte-identical")
        return 0

    differences = sync_skill_trees(args.source, args.mirror)
    count = len(validate_skills(args.source))
    if differences:
        print(f"skills: synchronized {len(differences)} difference(s) across {count} skill(s)")
    else:
        print(f"skills: {count} project skill(s) already synchronized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
