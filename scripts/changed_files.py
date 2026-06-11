"""Summary: List the source files changed versus a base ref, for the changed-files PR
gate and the `maintain` skill. CI's `changed` job and the local `lint-changed` /
`format-check-changed` Make targets call this to scope per-file checks (ruff, eslint,
prettier) to only what a PR touched. The changed set is the union of commits since the
base (three-dot merge-base diff), the working tree versus HEAD, and untracked files, so it
is correct both in CI (committed PR diff) and locally (uncommitted edits). The filtering
core is pure and git-free; git access goes through scripts/lib/gitio (an injectable
runner), so the contract is unit-tested without a repo. Output is deterministic (sorted,
de-duplicated, newline-separated).

Key classes:
- (none)

Key functions:
- filter_paths: keep paths under the rule's source scope matching a category's extensions.
- collect_changed: union of base-diff, working-tree, and untracked changes.
- main: CLI entry; print the changed files for a category, relative to a chosen root.

Notes:
- Categories: py (.py), ts (.ts/.tsx), all. Generated docs and infra are never returned.
- An unresolvable base ref degrades to working-tree-vs-HEAD (a warning, never a crash);
  source files under docs/reference/generated and infra/ are always excluded.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from pathlib import PurePosixPath

from lib.gitio import GitRunner, ref_exists, run_git, run_lines

DEFAULT_BASE = "origin/main"

# category -> the file extensions that category scopes to.
CATEGORY_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "py": (".py",),
    "ts": (".ts", ".tsx"),
    "all": (".py", ".ts", ".tsx"),
}

# Path *prefixes* (posix) that are never returned: generated docs and Terraform.
_EXCLUDED_PREFIXES: tuple[str, ...] = ("docs/reference/generated/", "infra/")
# Any path component matching one of these is excluded (build/venv/cache dirs).
_EXCLUDED_PARTS: frozenset[str] = frozenset(
    {
        ".venv",
        "node_modules",
        "build",
        "dist",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "coverage",
    }
)


def filter_paths(paths: Iterable[str], category: str) -> list[str]:
    """Return the sorted, unique paths in scope for a category (pure; no git/IO)."""
    extensions = CATEGORY_EXTENSIONS[category]
    kept: set[str] = set()
    for raw in paths:
        path = raw.strip()
        if not path or not path.endswith(extensions):
            continue
        posix = PurePosixPath(path)
        if any(path.startswith(prefix) for prefix in _EXCLUDED_PREFIXES):
            continue
        if _EXCLUDED_PARTS.intersection(posix.parts):
            continue
        kept.add(path)
    return sorted(kept)


def collect_changed(base: str, *, run: GitRunner = run_git) -> list[str]:
    """Return the raw changed-path union: commits since base + working tree + untracked."""
    changed: set[str] = set()
    # Working tree (staged + unstaged) vs HEAD — catches local, uncommitted edits.
    changed.update(run_lines(run, ["diff", "--name-only", "--diff-filter=ACMR", "HEAD"]))
    # Newly created, not-yet-tracked files (respects .gitignore) — caught locally too.
    changed.update(run_lines(run, ["ls-files", "--others", "--exclude-standard"]))
    # Commits on this branch since the merge base with `base` — the PR diff in CI.
    if ref_exists(base, run):
        changed.update(
            run_lines(run, ["diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD"])
        )
    elif base:
        print(
            f"changed_files: base ref {base!r} not found; using working tree only", file=sys.stderr
        )
    return sorted(changed)


def _relativize(paths: list[str], relative_to: str) -> list[str]:
    """Re-root repo-relative paths under relative_to (e.g. 'frontend'); drop those outside."""
    if not relative_to:
        return paths
    prefix = relative_to.rstrip("/") + "/"
    return [path[len(prefix) :] for path in paths if path.startswith(prefix)]


def main(argv: list[str] | None = None) -> int:
    """Print the changed source files for a category, one per line."""
    parser = argparse.ArgumentParser(description="List source files changed vs a base ref.")
    parser.add_argument("--category", choices=sorted(CATEGORY_EXTENSIONS), default="all")
    parser.add_argument("--base", default=DEFAULT_BASE, help="base ref (default origin/main)")
    parser.add_argument(
        "--relative-to", default="", help="emit paths relative to this repo subdirectory"
    )
    args = parser.parse_args(argv)

    in_scope = filter_paths(collect_changed(args.base), args.category)
    for path in _relativize(in_scope, args.relative_to):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
