"""Behavioral tests for repository-wide relative Markdown link validation."""

from __future__ import annotations

from pathlib import Path

import pytest

import check_docs_links
from check_docs_links import markdown_anchors, validate_docs_links


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_validates_the_exact_governed_surface_and_relative_anchors(sandbox: Path) -> None:
    _write(sandbox / "README.md", "# Home\n[Guide](docs/guide.md#how-it-works)\n")
    _write(sandbox / "AGENTS.md", "# Agents\n[Plans](plans/)\n")
    _write(sandbox / "docs" / "guide.md", "# Guide\n## How it works\n")
    _write(sandbox / "plans" / "README.md", "# Plans\n")
    assert validate_docs_links(sandbox) == []


def test_planted_dead_link_fails_with_source_line(sandbox: Path) -> None:
    _write(sandbox / "README.md", "# Home\n[Missing](docs/missing.md)\n")
    _write(sandbox / "AGENTS.md", "# Agents\n")
    issues = validate_docs_links(sandbox)
    assert [(issue.code, issue.line, issue.target) for issue in issues] == [
        ("missing_target", 2, "docs/missing.md")
    ]


def test_missing_anchor_and_repository_escape_fail(sandbox: Path) -> None:
    _write(
        sandbox / "README.md",
        "# Home\n[Bad anchor](guide.md#missing)\n[Escape](../outside.md)\n",
    )
    _write(sandbox / "AGENTS.md", "# Agents\n")
    _write(sandbox / "guide.md", "# Present\n")
    assert [issue.code for issue in validate_docs_links(sandbox)] == [
        "missing_anchor",
        "repository_escape",
    ]


def test_ignores_external_and_code_example_links(sandbox: Path) -> None:
    _write(
        sandbox / "README.md",
        "# Home\n[Web](https://example.com/missing)\n`[inline](missing.md)`\n"
        "```markdown\n[fenced](missing.md)\n```\n",
    )
    _write(sandbox / "AGENTS.md", "# Agents\n")
    assert validate_docs_links(sandbox) == []


def test_duplicate_heading_anchors_follow_github_suffixes(sandbox: Path) -> None:
    path = sandbox / "doc.md"
    _write(path, "# Phase 1 — Build\n# Phase 1 — Build\n# C++ & C#\n")
    assert markdown_anchors(path) == {"phase-1--build", "phase-1--build-1", "c--c"}


def test_plan_links_may_use_repository_root_paths_and_line_suffixes(sandbox: Path) -> None:
    _write(sandbox / "README.md", "# Home\n")
    _write(sandbox / "AGENTS.md", "# Agents\n")
    _write(sandbox / "backend" / "app.py", "value = 1\n")
    _write(sandbox / "plans" / "work.md", "# Plan\n[Code](backend/app.py:1)\n")
    assert validate_docs_links(sandbox) == []


def test_cli_returns_success_and_failure_with_actionable_output(
    sandbox: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write(sandbox / "README.md", "# Home\n")
    _write(sandbox / "AGENTS.md", "# Agents\n")
    assert check_docs_links.main(["--root", str(sandbox)]) == 0
    assert "docs-links-check OK" in capsys.readouterr().out

    _write(sandbox / "README.md", "# Home\n[Missing](missing.md)\n")
    assert check_docs_links.main(["--root", str(sandbox)]) == 1
    output = capsys.readouterr().out
    assert "README.md:2: missing_target" in output
    assert "docs-links-check FAILED: 1 invalid link(s)" in output
