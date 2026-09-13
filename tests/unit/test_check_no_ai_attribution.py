"""Behavioral tests for the AI-attribution guard (AGENTS.md Golden Rule 2)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_no_ai_attribution.sh"


def run_guard(
    *args: str, env_updates: dict[str, str] | None = None, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the guard with a controlled environment and capture its result."""
    env = os.environ.copy()
    env.pop("RANGE", None)
    env.update(env_updates or {})
    return subprocess.run(
        ["/bin/bash", str(SCRIPT), *args],
        cwd=cwd or REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def write_message(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "COMMIT_EDITMSG"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "trailer",
    [
        "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>",
        "Co-Authored-By: Claude <noreply@anthropic.com>",
        "co-authored-by: claude opus 5 <noreply@anthropic.com>",
        "Co-Authored-By: Copilot <copilot@github.com>",
        "Co-Authored-By: Cursor Agent <agent@cursor.sh>",
        "Co-Authored-By: dependabot[bot] <support@github.com>",
        "🤖 Generated with [Claude Code](https://claude.com/claude-code)",
    ],
)
def test_rejects_ai_attribution_in_message(tmp_path: Path, trailer: str) -> None:
    message = write_message(tmp_path, f"feat: add a thing\n\nSome body.\n\n{trailer}\n")
    result = run_guard("--message", str(message))
    assert result.returncode == 1
    assert "Golden Rule 2" in result.stderr


@pytest.mark.parametrize(
    "body",
    [
        "feat: add a thing\n",
        "feat: enhance Anthropic and OpenAI adapters with tool handling\n",
        "fix(api): enforce tenant scope\n\nCo-Authored-By: Kartik Hirijaganer <k@example.com>\n",
    ],
)
def test_accepts_human_authored_messages(tmp_path: Path, body: str) -> None:
    message = write_message(tmp_path, body)
    result = run_guard("--message", str(message))
    assert result.returncode == 0
    assert "no AI attribution" in result.stdout


def test_missing_message_file_is_a_usage_error(tmp_path: Path) -> None:
    result = run_guard("--message", str(tmp_path / "absent"))
    assert result.returncode == 2
    assert "usage:" in result.stderr


def test_rejects_extra_positional_arguments() -> None:
    result = run_guard("unexpected")
    assert result.returncode == 2
    assert "usage:" in result.stderr


def test_help_flag_exits_zero() -> None:
    result = run_guard("--help")
    assert result.returncode == 0
    assert "usage:" in result.stdout


def _git(repo: Path, *args: str, message_env: bool = False) -> None:
    env = os.environ.copy()
    if message_env:
        env.update(
            {
                "GIT_AUTHOR_NAME": "Test",
                "GIT_AUTHOR_EMAIL": "test@example.com",
                "GIT_COMMITTER_NAME": "Test",
                "GIT_COMMITTER_EMAIL": "test@example.com",
            }
        )
    subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)


@pytest.fixture
def scratch_repo(tmp_path: Path) -> Path:
    """A throwaway repo with one clean commit, used for history-range scanning."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "file.txt").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-q", "-m", "feat: initial commit", message_env=True)
    return repo


def test_history_scan_passes_on_clean_repo(scratch_repo: Path) -> None:
    result = run_guard(env_updates={"RANGE": "HEAD"}, cwd=scratch_repo)
    assert result.returncode == 0
    assert "No AI attribution" in result.stdout


def test_history_scan_reports_offending_commit_sha(scratch_repo: Path) -> None:
    (scratch_repo / "file.txt").write_text("world\n", encoding="utf-8")
    _git(scratch_repo, "add", "file.txt")
    _git(
        scratch_repo,
        "commit",
        "-q",
        "-m",
        "chore: update\n\nCo-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>",
        message_env=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=scratch_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    result = run_guard(env_updates={"RANGE": "HEAD"}, cwd=scratch_repo)
    assert result.returncode == 1
    assert head in result.stderr
    assert "Golden Rule 2" in result.stderr
