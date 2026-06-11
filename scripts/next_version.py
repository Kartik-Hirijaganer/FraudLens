"""Summary: Compute the next SemVer from Conventional Commits since the latest v* tag —
the propose-only engine behind `make version-next` and the `maintain` skill. It maps
feat -> minor, fix/perf -> patch, and a `!` marker or BREAKING CHANGE footer -> major,
takes the highest bump across the range, and prints the current/next version plus the
categorized commit subjects as JSON. It NEVER tags, commits, or pushes (Golden Rule 1);
the human reads the proposal and cuts the tag. The classification core is pure and
git-free; git access goes through scripts/lib/gitio (an injectable runner) so the logic is
unit-tested without a repo.

Key classes:
- (none)

Key functions:
- parse_commit_bump: the bump a single commit triggers (major/minor/patch/none).
- aggregate_bump: the highest-precedence bump across many commits.
- bump_version: apply a bump to an M.m.p version string.
- classify_commits: bucket commit subjects and return the aggregate bump.
- latest_tag: the newest v* tag, or None when the project is untagged.
- commits_since: (subject, body) pairs for commits after a tag (or all history).
- analyze: assemble the full version proposal as a JSON-ready dict.
- main: CLI entry; print the proposal as indented, sorted JSON.

Notes:
- Non-Conventional-Commit subjects contribute no bump (they are bucketed as "other").
- A 0.x project gets an advisory note: by convention a breaking change there often bumps
  the minor, not the major — the proposal still shows standard SemVer for the human to
  confirm or override.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterable
from typing import Any

from lib.gitio import GitRunner, run_git, run_lines

# Conventional Commit subject: type, optional (scope), optional ! breaking marker, colon.
_TYPE_RE = re.compile(r"^(?P<type>[a-z]+)(?:\([^)]*\))?(?P<bang>!)?:")
# BREAKING CHANGE / BREAKING-CHANGE footer (Conventional Commits spec) in the body.
_BREAKING_RE = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)
_PRECEDENCE: tuple[str, ...] = ("none", "patch", "minor", "major")
_MINOR_TYPES: frozenset[str] = frozenset({"feat"})
_PATCH_TYPES: frozenset[str] = frozenset({"fix", "perf"})


def parse_commit_bump(subject: str, body: str) -> str:
    """Return the bump a single commit triggers: major / minor / patch / none."""
    match = _TYPE_RE.match(subject.strip())
    if match is None:
        return "none"
    if match.group("bang") or _BREAKING_RE.search(body):
        return "major"
    commit_type = match.group("type")
    if commit_type in _MINOR_TYPES:
        return "minor"
    if commit_type in _PATCH_TYPES:
        return "patch"
    return "none"


def aggregate_bump(bumps: Iterable[str]) -> str:
    """Return the highest-precedence bump across commits (major > minor > patch > none)."""
    best = "none"
    for bump in bumps:
        if _PRECEDENCE.index(bump) > _PRECEDENCE.index(best):
            best = bump
    return best


def bump_version(current: str, bump: str) -> str:
    """Apply bump to an 'M.m.p' version string, returning the new version string."""
    major, minor, patch = (int(part) for part in current.split("."))
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    return current


def classify_commits(commits: list[tuple[str, str]]) -> dict[str, Any]:
    """Bucket commit subjects by triggered bump and return the aggregate bump."""
    buckets: dict[str, list[str]] = {"breaking": [], "feat": [], "fix": [], "other": []}
    bucket_for = {"major": "breaking", "minor": "feat", "patch": "fix", "none": "other"}
    bumps: list[str] = []
    for subject, body in commits:
        bump = parse_commit_bump(subject, body)
        bumps.append(bump)
        buckets[bucket_for[bump]].append(subject)
    return {"bump": aggregate_bump(bumps), "commits": buckets}


def latest_tag(*, run: GitRunner = run_git) -> str | None:
    """Return the newest v* tag by SemVer order, or None when the project is untagged."""
    tags = run_lines(run, ["tag", "--list", "v*", "--sort=-v:refname"])
    return tags[0] if tags else None


def commits_since(tag: str | None, *, run: GitRunner = run_git) -> list[tuple[str, str]]:
    """Return (subject, body) pairs for commits after tag (or all history when None)."""
    commit_range = f"{tag}..HEAD" if tag else "HEAD"
    raw = run(["log", commit_range, "--format=%s%x1f%b%x1e"])
    commits: list[tuple[str, str]] = []
    for record in raw.split("\x1e"):
        if not record.strip():
            continue
        subject, _, body = record.lstrip("\n").partition("\x1f")
        commits.append((subject.strip(), body))
    return commits


def analyze(*, run: GitRunner = run_git) -> dict[str, Any]:
    """Assemble the full version proposal (current/next version, bump, commits) as a dict."""
    tag = latest_tag(run=run)
    current = tag.lstrip("v") if tag else "0.0.0"
    classified = classify_commits(commits_since(tag, run=run))
    bump = classified["bump"]
    notes: list[str] = []
    if tag is None:
        notes.append("no v* tag found; treating all history as unreleased from 0.0.0")
    if bump == "major" and current.startswith("0."):
        notes.append(
            "current major is 0; by 0.x convention a breaking change often bumps the minor "
            "(0.x+1.0), not 1.0.0 — confirm intent before tagging"
        )
    return {
        "latest_tag": tag,
        "current_version": current,
        "bump": bump,
        "next_version": bump_version(current, bump),
        "release_needed": bump != "none",
        "commits": classified["commits"],
        "notes": notes,
    }


def main(argv: list[str] | None = None) -> int:
    """Print the version proposal (propose-only; never tags). JSON, or just the next tag."""
    parser = argparse.ArgumentParser(description="Propose the next SemVer (Conventional Commits).")
    parser.add_argument(
        "--format",
        choices=["json", "tag"],
        default="json",
        help="json (full proposal) or tag (just 'v<next_version>', for git-cliff --tag)",
    )
    args = parser.parse_args(argv)
    proposal = analyze()
    if args.format == "tag":
        print(f"v{proposal['next_version']}")
    else:
        print(json.dumps(proposal, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
