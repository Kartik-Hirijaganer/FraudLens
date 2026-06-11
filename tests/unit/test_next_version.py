"""Unit tests for scripts/next_version.py (Conventional-Commit SemVer proposal)."""

from __future__ import annotations

from collections.abc import Callable

from next_version import (
    aggregate_bump,
    analyze,
    bump_version,
    classify_commits,
    commits_since,
    latest_tag,
    parse_commit_bump,
)

US = "\x1f"  # subject/body separator in the git log format
RS = "\x1e"  # record separator


# --------------------------------------------------------------------------- parse_commit_bump
def test_parse_feat_is_minor() -> None:
    assert parse_commit_bump("feat: add health check", "") == "minor"


def test_parse_fix_and_perf_are_patch() -> None:
    assert parse_commit_bump("fix(api): handle 404", "") == "patch"
    assert parse_commit_bump("perf: speed up scan", "") == "patch"


def test_parse_bang_marker_is_major() -> None:
    assert parse_commit_bump("feat!: drop legacy field", "") == "major"


def test_parse_breaking_footer_is_major_both_spellings() -> None:
    assert parse_commit_bump("feat: x", "body\n\nBREAKING CHANGE: removed Y") == "major"
    assert parse_commit_bump("fix: x", "BREAKING-CHANGE: removed Z") == "major"


def test_parse_chore_and_nonconventional_are_none() -> None:
    assert parse_commit_bump("chore: tidy", "") == "none"
    assert parse_commit_bump("just some words", "") == "none"


# --------------------------------------------------------------------------- aggregate_bump
def test_aggregate_takes_highest_precedence() -> None:
    assert aggregate_bump(["patch", "minor", "none"]) == "minor"
    assert aggregate_bump(["patch", "major"]) == "major"
    assert aggregate_bump([]) == "none"


# --------------------------------------------------------------------------- bump_version
def test_bump_version_each_level() -> None:
    assert bump_version("0.1.0", "minor") == "0.2.0"
    assert bump_version("0.1.0", "major") == "1.0.0"
    assert bump_version("1.2.3", "patch") == "1.2.4"
    assert bump_version("1.2.3", "none") == "1.2.3"


# --------------------------------------------------------------------------- classify_commits
def test_classify_buckets_and_aggregates() -> None:
    result = classify_commits([("feat: a", ""), ("fix: b", ""), ("chore: c", "")])
    assert result["bump"] == "minor"
    assert result["commits"] == {
        "breaking": [],
        "feat": ["feat: a"],
        "fix": ["fix: b"],
        "other": ["chore: c"],
    }


# --------------------------------------------------------------------------- git wrappers
def test_latest_tag_picks_first_or_none() -> None:
    assert latest_tag(run=lambda _a: "v0.2.0\nv0.1.0\n") == "v0.2.0"
    assert latest_tag(run=lambda _a: "") is None


def test_commits_since_parses_records() -> None:
    raw = f"feat: a{US}body a{RS}fix: b{US}{RS}"
    assert commits_since("v0.1.0", run=lambda _a: raw) == [("feat: a", "body a"), ("fix: b", "")]


def _fake_run(tag_line: str, log_raw: str) -> Callable[[list[str]], str]:
    def run(args: list[str]) -> str:
        if args[0] == "tag":
            return tag_line
        if args[0] == "log":
            return log_raw
        return ""

    return run


# --------------------------------------------------------------------------- analyze
def test_analyze_untagged_project_starts_from_zero() -> None:
    result = analyze(run=_fake_run("", f"feat: first{US}{RS}"))
    assert result["latest_tag"] is None
    assert result["current_version"] == "0.0.0"
    assert result["next_version"] == "0.1.0"
    assert result["release_needed"] is True
    assert any("no v* tag" in note for note in result["notes"])


def test_analyze_flags_breaking_change_in_zero_dot_x() -> None:
    result = analyze(run=_fake_run("v0.3.0\n", f"feat!: drop field{US}{RS}"))
    assert result["bump"] == "major"
    assert result["next_version"] == "1.0.0"
    assert any("0.x convention" in note for note in result["notes"])


def test_analyze_no_release_when_only_chores() -> None:
    result = analyze(run=_fake_run("v0.3.0\n", f"chore: deps{US}{RS}"))
    assert result["bump"] == "none"
    assert result["release_needed"] is False
    assert result["next_version"] == "0.3.0"
