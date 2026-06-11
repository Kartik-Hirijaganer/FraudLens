"""Unit tests for scripts/changed_files.py (the changed-files PR-gate helper)."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pytest


def _load_changed_files() -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "changed_files.py"
    spec = spec_from_file_location("changed_files_for_test", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CF = _load_changed_files()


# --------------------------------------------------------------------------- filter_paths
def test_filter_paths_py_category_keeps_python_only() -> None:
    paths = ["backend/src/a.py", "frontend/src/b.tsx", "scripts/c.py"]
    assert CF.filter_paths(paths, "py") == ["backend/src/a.py", "scripts/c.py"]


def test_filter_paths_ts_category_keeps_ts_and_tsx() -> None:
    paths = ["frontend/src/a.ts", "frontend/src/b.tsx", "backend/src/c.py"]
    assert CF.filter_paths(paths, "ts") == ["frontend/src/a.ts", "frontend/src/b.tsx"]


def test_filter_paths_all_category_keeps_every_source_extension() -> None:
    paths = ["a.py", "b.ts", "c.tsx", "d.md", "e.json"]
    assert CF.filter_paths(paths, "all") == ["a.py", "b.ts", "c.tsx"]


def test_filter_paths_excludes_generated_and_infra_prefixes() -> None:
    paths = [
        "docs/reference/generated/api/openapi.py",
        "infra/terraform/x.py",
        "backend/src/keep.py",
    ]
    assert CF.filter_paths(paths, "py") == ["backend/src/keep.py"]


def test_filter_paths_excludes_vendored_and_cache_dirs() -> None:
    paths = [
        "frontend/node_modules/pkg/index.ts",
        "backend/.venv/lib/x.py",
        "scripts/__pycache__/cached.py",
        "backend/src/keep.py",
    ]
    assert CF.filter_paths(paths, "all") == ["backend/src/keep.py"]


def test_filter_paths_sorts_and_deduplicates() -> None:
    paths = ["b.py", "a.py", "b.py", "  ", ""]
    assert CF.filter_paths(paths, "py") == ["a.py", "b.py"]


# --------------------------------------------------------------------------- _relativize
def test_relativize_reroots_under_subdirectory_and_drops_outside() -> None:
    paths = ["frontend/src/a.tsx", "backend/src/b.py"]
    assert CF._relativize(paths, "frontend") == ["src/a.tsx"]


def test_relativize_noop_without_root() -> None:
    paths = ["frontend/src/a.tsx"]
    assert CF._relativize(paths, "") == paths


# --------------------------------------------------------------------------- collect_changed
def _fake_runner(
    *, base_exists: bool, worktree: list[str], untracked: list[str], base_diff: list[str]
):
    """Build a GitRunner stub keyed on the git subcommand changed_files.py issues."""

    def run(args: list[str]) -> str:
        if args[0] == "rev-parse":
            return "abcdef\n" if base_exists else ""
        if args[:2] == ["diff", "--name-only"] and args[-1] == "HEAD":
            return "\n".join(worktree) + "\n"
        if args[:2] == ["ls-files", "--others"]:
            return "\n".join(untracked) + "\n"
        if args[:2] == ["diff", "--name-only"]:  # the `base...HEAD` diff
            return "\n".join(base_diff) + "\n"
        return ""

    return run


def test_collect_changed_unions_worktree_untracked_and_base_diff() -> None:
    run = _fake_runner(
        base_exists=True,
        worktree=["a.py"],
        untracked=["b.py"],
        base_diff=["c.py", "a.py"],
    )
    assert CF.collect_changed("origin/main", run=run) == ["a.py", "b.py", "c.py"]


def test_collect_changed_skips_base_diff_when_ref_missing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run = _fake_runner(
        base_exists=False, worktree=["a.py"], untracked=[], base_diff=["should-not-appear.py"]
    )
    result = CF.collect_changed("origin/nope", run=run)
    assert result == ["a.py"]
    assert "not found" in capsys.readouterr().err


# --------------------------------------------------------------------------- main (CLI)
def test_main_prints_filtered_relativized_paths(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        CF, "collect_changed", lambda base: ["frontend/src/a.tsx", "backend/src/b.py"]
    )
    exit_code = CF.main(["--category", "ts", "--relative-to", "frontend"])
    assert exit_code == 0
    assert capsys.readouterr().out.splitlines() == ["src/a.tsx"]
