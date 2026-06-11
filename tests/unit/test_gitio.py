"""Unit tests for scripts/lib/gitio.py (shared read-only git helpers)."""

from __future__ import annotations

from lib.gitio import ref_exists, run_git, run_lines


def test_run_git_returns_stdout_on_success() -> None:
    assert run_git(["rev-parse", "--show-toplevel"]).strip().endswith("FraudLens")


def test_run_git_returns_empty_on_failure() -> None:
    assert run_git(["not-a-real-git-subcommand"]) == ""


def test_run_lines_strips_blank_lines() -> None:
    assert run_lines(lambda _args: "a\n\n  \nb\n", ["whatever"]) == ["a", "b"]


def test_ref_exists_true_when_runner_yields_a_sha() -> None:
    assert ref_exists("origin/main", lambda _args: "abcdef\n") is True


def test_ref_exists_false_when_runner_is_empty() -> None:
    assert ref_exists("origin/nope", lambda _args: "") is False
