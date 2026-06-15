"""Release dry-run / flow contract tests (plan §16 Phase 15 / §20). These assert the
release would proceed correctly WITHOUT cutting a tag: every declared version agrees, the
CHANGELOG carries the release section, the §20 automation is wired, and release.yml
re-runs the CI gate before publishing (a tag only ships from green, rule 9). Structural,
like test_deploy_flow — no tag is pushed, no release is published. Run via `pytest -k release`.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from release_gate import collect_versions, evaluate

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _read(rel: str) -> str:
    """Read a repo-relative file, returning '' when absent (the release-gate reader contract)."""
    path = REPO_ROOT / rel
    return path.read_text(encoding="utf-8") if path.exists() else ""


# --- The gate passes on the committed tree (the actual release dry-run) ------------------------


def test_release_gate_passes_on_the_committed_repo() -> None:
    """Every automatable §20 check passes against the real tree; a consistent version resolves."""
    result = evaluate(_read)
    failed = [check["name"] for check in result["checks"] if not check["ok"]]
    assert result["passed"], f"failing release-gate checks: {failed}"
    assert result["version"]


def test_all_declared_versions_agree() -> None:
    """The release version is one fact across all seven sources (rule 5: no duplication drift)."""
    versions = collect_versions(_read)
    assert None not in versions.values(), versions
    assert len(set(versions.values())) == 1, versions


# --- release.yml: tag-from-green + changelog (the safe-release wiring) --------------------------


def test_release_workflow_reruns_ci_before_publishing() -> None:
    """The release job depends on a verify job that re-runs the reusable CI gate."""
    jobs = yaml.safe_load((WORKFLOWS / "release.yml").read_text())["jobs"]
    assert jobs["verify"]["uses"].endswith("_ci-reusable.yml")
    assert jobs["release"]["needs"] == "verify"


def test_release_is_tag_triggered_and_generates_changelog() -> None:
    """release.yml fires on v* tags and generates notes with git-cliff (text-level: avoids the
    PyYAML `on:` -> bool key pitfall)."""
    text = (WORKFLOWS / "release.yml").read_text()
    assert 'tags: ["v*"]' in text
    assert "git-cliff" in text


# --- dependency-update: Renovate PRs run the SAME gate -----------------------------------------


def test_dependency_update_runs_the_same_gate_on_renovate_branches() -> None:
    """Renovate PRs run the SAME reusable CI gate (no separate weaker path); rule 8 / Golden 1."""
    text = (WORKFLOWS / "dependency-update.yml").read_text()
    assert yaml.safe_load(text)["jobs"]["ci"]["uses"].endswith("_ci-reusable.yml")
    assert "renovate/" in text
