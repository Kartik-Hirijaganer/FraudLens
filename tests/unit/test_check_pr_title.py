"""Behavioral tests for the shared local/GitHub PR-title validator."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_pr_title.sh"


def run_title_check(
    *args: str, env_updates: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the validator with a controlled environment and capture its result."""
    env = os.environ.copy()
    env.pop("PR_TITLE", None)
    env.update(env_updates or {})
    return subprocess.run(
        ["/bin/bash", str(SCRIPT), *args],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "title",
    [
        "feat: add health check",
        "fix(api): enforce tenant scope",
        "chore(release)!: publish 1.0.0",
        "refactor(api/v1): simplify routing",
    ],
)
def test_accepts_conventional_commit_titles(title: str) -> None:
    result = run_title_check(title)
    assert result.returncode == 0
    assert f": {title}" in result.stdout


@pytest.mark.parametrize(
    "title",
    [
        "Release/v0.2.0",
        "Feature: add health check",
        "feat add health check",
        "feat:",
    ],
)
def test_rejects_non_conventional_titles(title: str) -> None:
    result = run_title_check(title)
    assert result.returncode == 1
    assert "must follow Conventional Commits" in result.stderr
    assert f"Actual PR title: {title}" in result.stderr


def test_reads_title_from_environment() -> None:
    result = run_title_check(env_updates={"PR_TITLE": "docs: explain PR checks"})
    assert result.returncode == 0
    assert "(PR_TITLE)" in result.stdout


def test_reads_current_branch_pr_title_from_gh() -> None:
    with TemporaryDirectory() as directory:
        fake_gh = Path(directory) / "gh"
        fake_gh.write_text("#!/bin/sh\nprintf '%s\\n' 'ci: mirror PR checks locally'\n")
        fake_gh.chmod(0o755)

        result = run_title_check(env_updates={"PATH": directory})

    assert result.returncode == 0
    assert "(open PR for the current branch)" in result.stdout


def test_missing_title_fails_with_actionable_usage() -> None:
    with TemporaryDirectory() as directory:
        result = run_title_check(env_updates={"PATH": directory})

    assert result.returncode == 2
    assert "PR title is required" in result.stderr
    assert "PR_TITLE='feat: add health check'" in result.stderr
