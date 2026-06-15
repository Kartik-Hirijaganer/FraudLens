"""Unit tests for scripts/release_gate.py (the §20 release-gate verifier).

The gate logic is exercised against in-memory fake file readers so every pass and fail
path is deterministic and independent of the real tree. The against-the-real-repo
"release dry-run" lives in tests/integration/test_release_flow.py.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

import release_gate
from release_gate import (
    check_changelog,
    check_changelog_config,
    check_dependency_update,
    check_make_targets,
    check_release_workflow,
    check_renovate,
    check_versions,
    collect_versions,
    evaluate,
    main,
)

_PYPROJECT = '[project]\nname = "pkg"\nversion = "{v}"\n'
_PACKAGE_JSON = '{{"name": "fraudlens-frontend", "version": "{v}"}}'
_INIT = '"""doc."""\n__version__ = "{v}"\n'
_CHANGELOG = "# Changelog\n\n## [Unreleased]\n\n## [{v}] - 2026-06-15\n\n- first release\n"
_RELEASE_YML = (
    'on:\n  push:\n    tags: ["v*"]\n'
    "jobs:\n"
    "  verify:\n    uses: ./.github/workflows/_ci-reusable.yml\n"
    "  release:\n    needs: verify\n    steps:\n"
    "      - uses: orhun/git-cliff-action@v4\n"
)
_CLIFF = "[git]\nconventional_commits = true\n"
_RENOVATE = '{"packageRules": [{"matchUpdateTypes": ["major"], "automerge": false}]}'
_DEPUP_YML = (
    "jobs:\n  ci:\n    if: ${{ startsWith(github.head_ref, 'renovate/') }}\n"
    "    uses: ./.github/workflows/_ci-reusable.yml\n"
)
_MAKEFILE = "\n".join(
    f"{t}:\n\t@echo {t}"
    for t in ("ci", "docs-check", "tf-validate", "docker-build", "local-demo-smoke")
)

_VERSION_PATHS = (
    "pyproject.toml",
    "backend/pyproject.toml",
    "packages/fraudlens-core/pyproject.toml",
    "packages/fraudlens-llm/pyproject.toml",
    "packages/fraudlens-ml/pyproject.toml",
)


def good_files(version: str = "1.0.0") -> dict[str, str]:
    """A complete, release-ready set of file contents at one version."""
    files = {path: _PYPROJECT.format(v=version) for path in _VERSION_PATHS}
    files.update(
        {
            "frontend/package.json": _PACKAGE_JSON.format(v=version),
            "backend/src/fraudlens_backend/__init__.py": _INIT.format(v=version),
            "CHANGELOG.md": _CHANGELOG.format(v=version),
            ".github/workflows/release.yml": _RELEASE_YML,
            "cliff.toml": _CLIFF,
            "renovate.json": _RENOVATE,
            ".github/workflows/dependency-update.yml": _DEPUP_YML,
            "Makefile": _MAKEFILE,
        }
    )
    return files


def reader_for(files: dict[str, str]) -> Callable[[str], str]:
    """A reader returning each path's text, or '' for anything absent (like the real one)."""
    return lambda rel: files.get(rel, "")


# --------------------------------------------------------------------------- collect_versions
def test_collect_versions_reads_every_source() -> None:
    versions = collect_versions(reader_for(good_files("1.2.3")))
    assert len(versions) == 7
    assert set(versions.values()) == {"1.2.3"}


def test_collect_versions_returns_none_for_missing_or_malformed() -> None:
    files = good_files()
    files["pyproject.toml"] = "not valid toml ::: ["
    files["frontend/package.json"] = "{not json"
    del files["backend/src/fraudlens_backend/__init__.py"]
    versions = collect_versions(reader_for(files))
    assert versions["root pyproject"] is None
    assert versions["frontend package.json"] is None
    assert versions["backend __version__"] is None


# --------------------------------------------------------------------------- check_versions
def test_check_versions_passes_when_all_agree() -> None:
    result = check_versions(collect_versions(reader_for(good_files("1.0.0"))))
    assert result["ok"] is True
    assert "1.0.0" in result["detail"]


def test_check_versions_honours_expected_target() -> None:
    versions = collect_versions(reader_for(good_files("1.0.0")))
    assert check_versions(versions, expected="1.0.0")["ok"] is True
    mismatch = check_versions(versions, expected="2.0.0")
    assert mismatch["ok"] is False
    assert "expected 2.0.0" in mismatch["detail"]


def test_check_versions_fails_on_missing_source() -> None:
    files = good_files()
    del files["backend/pyproject.toml"]
    result = check_versions(collect_versions(reader_for(files)))
    assert result["ok"] is False
    assert "missing" in result["detail"]


def test_check_versions_fails_when_sources_disagree() -> None:
    files = good_files("1.0.0")
    files["frontend/package.json"] = _PACKAGE_JSON.format(v="0.9.0")
    result = check_versions(collect_versions(reader_for(files)))
    assert result["ok"] is False
    assert "disagree" in result["detail"]


# --------------------------------------------------------------------------- check_changelog
def test_check_changelog_finds_the_section() -> None:
    assert check_changelog(reader_for(good_files("1.0.0")), "1.0.0")["ok"] is True


def test_check_changelog_fails_without_the_section() -> None:
    assert check_changelog(reader_for(good_files("1.0.0")), "2.0.0")["ok"] is False


def test_check_changelog_fails_when_version_is_none() -> None:
    assert check_changelog(reader_for(good_files()), None)["ok"] is False


# --------------------------------------------------------------------------- check_release_workflow
def test_check_release_workflow_passes_when_wired() -> None:
    assert check_release_workflow(reader_for(good_files()))["ok"] is True


@pytest.mark.parametrize("drop", ["v*", "_ci-reusable.yml", "git-cliff"])
def test_check_release_workflow_fails_when_a_part_is_missing(drop: str) -> None:
    files = good_files()
    files[".github/workflows/release.yml"] = _RELEASE_YML.replace(drop, "REMOVED")
    assert check_release_workflow(reader_for(files))["ok"] is False


# --------------------------------------------------------------------------- check_renovate
def test_check_renovate_passes_when_majors_need_review() -> None:
    assert check_renovate(reader_for(good_files()))["ok"] is True


def test_check_renovate_fails_when_majors_automerge() -> None:
    files = good_files()
    files["renovate.json"] = (
        '{"packageRules": [{"matchUpdateTypes": ["major"], "automerge": true}]}'
    )
    assert check_renovate(reader_for(files))["ok"] is False


def test_check_renovate_fails_on_invalid_json() -> None:
    files = good_files()
    files["renovate.json"] = "{not json"
    assert check_renovate(reader_for(files))["ok"] is False


# --------------------------------------------------------------------------- other wiring checks
def test_check_dependency_update_requires_reusable_gate_on_renovate() -> None:
    assert check_dependency_update(reader_for(good_files()))["ok"] is True
    files = good_files()
    files[".github/workflows/dependency-update.yml"] = "jobs:\n  ci:\n    uses: ./other.yml\n"
    assert check_dependency_update(reader_for(files))["ok"] is False


def test_check_changelog_config_requires_conventional_commits() -> None:
    assert check_changelog_config(reader_for(good_files()))["ok"] is True
    files = good_files()
    files["cliff.toml"] = "[git]\nconventional_commits = false\n"
    assert check_changelog_config(reader_for(files))["ok"] is False


def test_check_make_targets_requires_all_gate_targets() -> None:
    assert check_make_targets(reader_for(good_files()))["ok"] is True
    files = good_files()
    files["Makefile"] = _MAKEFILE.replace("tf-validate:", "tf-skip:")
    result = check_make_targets(reader_for(files))
    assert result["ok"] is False
    assert "tf-validate" in result["detail"]


# --------------------------------------------------------------------------- evaluate
def test_evaluate_passes_for_a_release_ready_tree() -> None:
    result = evaluate(reader_for(good_files("1.0.0")))
    assert result["passed"] is True
    assert result["version"] == "1.0.0"
    assert all(check["ok"] for check in result["checks"])
    # The human-owned items are reported (never auto-passed) and reference the version.
    assert any("1.0.0" in item for item in result["manual"])


def test_evaluate_fails_and_keeps_version_none_on_inconsistency() -> None:
    files = good_files("1.0.0")
    files["frontend/package.json"] = _PACKAGE_JSON.format(v="0.9.0")
    result = evaluate(reader_for(files))
    assert result["passed"] is False
    assert result["version"] is None
    # Manual checklist still renders with a placeholder when no version resolved.
    assert any("X.Y.Z" in item for item in result["manual"])


# --------------------------------------------------------------------------- main (CLI)
def test_main_exits_zero_and_emits_json_when_gate_passes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(release_gate, "_repo_reader", reader_for(good_files("1.0.0")))
    assert main([]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True
    assert payload["version"] == "1.0.0"


def test_main_text_format_renders_a_checklist(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(release_gate, "_repo_reader", reader_for(good_files("1.0.0")))
    assert main(["--format", "text"]) == 0
    out = capsys.readouterr().out
    assert "PASS" in out
    assert "Manual (human-owned" in out


def test_main_exits_nonzero_when_gate_unmet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_gate, "_repo_reader", reader_for({}))
    assert main([]) == 1


def test_main_expect_flag_fails_on_version_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_gate, "_repo_reader", reader_for(good_files("1.0.0")))
    assert main(["--expect", "9.9.9"]) == 1
