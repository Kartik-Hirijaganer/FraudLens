"""Behavioral tests for the configured physical-line cap and shrink-only baseline."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

import check_file_length
from lib.file_length import (
    BaselineEntry,
    FileLengthBaseline,
    FileLengthSettings,
    check_file_lengths,
    count_physical_lines,
    load_baseline,
    load_file_length_settings,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PLANTED_LINES = 501


def _settings(*, baseline_file: str | None = None) -> FileLengthSettings:
    return FileLengthSettings(
        max_lines=500,
        roots=("src",),
        extensions=(".py", ".ts"),
        exclude_globs=("**/generated/**",),
        baseline_file=baseline_file,
    )


def _write_lines(path: Path, count: int, *, trailing_newline: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = "\n" if trailing_newline else ""
    path.write_text("\n".join("line" for _ in range(count)) + suffix, encoding="utf-8")


def test_committed_baseline_captures_exactly_the_33_current_offenders() -> None:
    settings = load_file_length_settings(REPO_ROOT)
    baseline = load_baseline(REPO_ROOT, settings)
    assert len(baseline.entries) == 33
    assert check_file_lengths(REPO_ROOT, settings, baseline) == []
    for entry in baseline.entries:
        assert count_physical_lines(REPO_ROOT / entry.path) == entry.lines
        assert entry.lines > settings.max_lines


def test_unlisted_501_line_source_fails_the_absolute_cap(sandbox: Path) -> None:
    source = sandbox / "src" / "oversized.py"
    _write_lines(source, PLANTED_LINES)
    violations = check_file_lengths(sandbox, _settings(), FileLengthBaseline())
    assert [(item.code, item.current_lines) for item in violations] == [
        ("over_limit", PLANTED_LINES)
    ]


def test_baselined_file_may_only_shrink_until_it_is_compliant(sandbox: Path) -> None:
    source = sandbox / "src" / "ratcheted.py"
    baseline_lines = 550
    baseline = FileLengthBaseline(
        entries=(BaselineEntry(path="src/ratcheted.py", lines=baseline_lines),)
    )
    _write_lines(source, baseline_lines - 1)
    assert check_file_lengths(sandbox, _settings(), baseline) == []

    _write_lines(source, baseline_lines + 1)
    assert check_file_lengths(sandbox, _settings(), baseline)[0].code == "baseline_growth"

    _write_lines(source, 500)
    assert check_file_lengths(sandbox, _settings(), baseline)[0].code == (
        "compliant_baseline_entry"
    )


def test_missing_or_excluded_baseline_path_is_stale(sandbox: Path) -> None:
    baseline = FileLengthBaseline(entries=(BaselineEntry(path="src/missing.py", lines=600),))
    violation = check_file_lengths(sandbox, _settings(), baseline)[0]
    assert violation.code == "stale_baseline_entry"
    assert violation.current_lines is None


def test_discovery_honors_extensions_and_exclusions(sandbox: Path) -> None:
    _write_lines(sandbox / "src" / "small.py", 10)
    _write_lines(sandbox / "src" / "notes.md", PLANTED_LINES)
    _write_lines(sandbox / "src" / "generated" / "large.ts", PLANTED_LINES)
    assert check_file_lengths(sandbox, _settings(), FileLengthBaseline()) == []


def test_line_count_matches_wc_newline_semantics(sandbox: Path) -> None:
    path = sandbox / "source.py"
    _write_lines(path, 3, trailing_newline=False)
    assert count_physical_lines(path) == 2
    _write_lines(path, 3)
    assert count_physical_lines(path) == 3


def test_policy_rejects_escaping_paths_and_duplicate_baseline_entries() -> None:
    with pytest.raises(ValidationError):
        FileLengthSettings(
            max_lines=500,
            roots=("../outside",),
            extensions=(".py",),
            exclude_globs=(),
        )
    with pytest.raises(ValidationError):
        FileLengthBaseline(
            entries=(
                BaselineEntry(path="src/a.py", lines=600),
                BaselineEntry(path="src/a.py", lines=550),
            )
        )


def test_cli_reports_the_committed_ratchet_as_compliant(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert check_file_length.main([]) == 0
    assert "file-length-check OK" in capsys.readouterr().out
