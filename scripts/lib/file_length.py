"""Summary: Baseline-ratcheting physical-line gate for FraudLens source files. The
quality document defines roots, source extensions, exclusions, and an optional
temporary baseline. Files above the cap may only shrink from their recorded count;
once compliant they must leave the baseline. Counts intentionally match `wc -l`.

Key classes:
- FileLengthSettings: validated discovery and cap policy from config/quality.yaml.
- QualityConfig: extensible Pydantic boundary for the shared quality document.
- BaselineEntry: one temporarily grandfathered oversized source file.
- FileLengthBaseline: validated collection of temporary baseline entries.
- FileLengthViolation: one actionable line-cap or baseline-ratchet failure.

Key functions:
- load_file_length_settings: validate and resolve the configured file-length policy.
- load_baseline: validate the optional shrink-only baseline document.
- iter_source_files: yield all source files governed by the configured policy.
- count_physical_lines: count newline bytes exactly like wc -l.
- check_file_lengths: return every cap and baseline-ratchet violation.

Notes:
- Phase 2 removes the baseline; the same engine then enforces 500 lines absolutely.
- Symlinks and paths outside the repository are rejected or ignored during discovery.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_QUALITY_CONFIG = Path("config/quality.yaml")


class FileLengthSettings(BaseModel):
    """Validated source-discovery and physical-line policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_lines: int = Field(..., gt=0, description="Maximum physical lines in a source file.")
    roots: tuple[str, ...] = Field(..., min_length=1, description="Repository-relative roots.")
    extensions: tuple[str, ...] = Field(
        ..., min_length=1, description="Source suffixes, each including its leading dot."
    )
    exclude_globs: tuple[str, ...] = Field(
        default=(), description="Repository-relative source paths exempt from the gate."
    )
    baseline_file: str | None = Field(
        default=None, description="Optional repository-relative shrink-only baseline YAML."
    )

    @field_validator("roots", "exclude_globs")
    @classmethod
    def _paths_are_relative(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            path = PurePosixPath(value)
            if not value.strip() or path.is_absolute() or ".." in path.parts:
                raise ValueError("configured paths and globs must stay repository-relative")
        return values

    @field_validator("extensions")
    @classmethod
    def _extensions_have_dots(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.startswith(".") or "/" in value for value in values):
            raise ValueError("source extensions must start with a dot and contain no path")
        return values

    @field_validator("baseline_file")
    @classmethod
    def _baseline_is_relative(cls, value: str | None) -> str | None:
        if value is None:
            return value
        path = PurePosixPath(value)
        if not value.strip() or path.is_absolute() or ".." in path.parts:
            raise ValueError("baseline_file must stay repository-relative")
        return value


class QualityConfig(BaseModel):
    """The Phase-0 portion of the extensible quality policy document."""

    model_config = ConfigDict(extra="allow", frozen=True)

    file_length: FileLengthSettings = Field(..., description="Physical source-file length policy.")


class BaselineEntry(BaseModel):
    """One temporarily grandfathered oversized source file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(..., min_length=1, description="Repository-relative source path.")
    lines: int = Field(..., gt=0, description="Maximum allowed line count during the ratchet.")

    @field_validator("path")
    @classmethod
    def _path_is_relative(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("baseline paths must stay repository-relative")
        return value


class FileLengthBaseline(BaseModel):
    """The temporary baseline with unique source paths."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entries: tuple[BaselineEntry, ...] = Field(
        default=(), description="Oversized files and their shrink-only ceilings."
    )

    @model_validator(mode="after")
    def _paths_are_unique(self) -> FileLengthBaseline:
        paths = [entry.path for entry in self.entries]
        if len(paths) != len(set(paths)):
            raise ValueError("baseline paths must be unique")
        return self


class FileLengthViolation(BaseModel):
    """One cap, ratchet, or stale-baseline violation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(..., description="Repository-relative path that failed.")
    code: str = Field(..., description="Stable machine-readable violation code.")
    current_lines: int | None = Field(..., description="Observed wc-compatible line count.")
    baseline_lines: int | None = Field(..., description="Recorded ratchet ceiling, if any.")
    message: str = Field(..., description="Actionable human-readable failure detail.")


def _load_yaml(path: Path) -> Any:
    """Load one YAML document without weakening Pydantic validation."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_file_length_settings(
    repo_root: Path, config_path: Path = DEFAULT_QUALITY_CONFIG
) -> FileLengthSettings:
    """Load and validate the file-length section of the quality document."""
    resolved = config_path if config_path.is_absolute() else repo_root / config_path
    return QualityConfig.model_validate(_load_yaml(resolved)).file_length


def load_baseline(repo_root: Path, settings: FileLengthSettings) -> FileLengthBaseline:
    """Load the configured baseline, or return an empty baseline when disabled."""
    if settings.baseline_file is None:
        return FileLengthBaseline()
    path = repo_root / settings.baseline_file
    if not path.is_file():
        raise FileNotFoundError(f"configured file-length baseline does not exist: {path}")
    return FileLengthBaseline.model_validate(_load_yaml(path))


def _is_excluded(relative: str, patterns: tuple[str, ...]) -> bool:
    """Match repository-relative POSIX paths against configured glob spellings."""
    path = PurePosixPath(relative)
    return any(
        path.match(pattern) or fnmatch.fnmatchcase(relative, pattern) for pattern in patterns
    )


def iter_source_files(repo_root: Path, settings: FileLengthSettings) -> Iterator[Path]:
    """Yield governed source files in stable repository-relative order."""
    discovered: set[Path] = set()
    root = repo_root.resolve()
    for configured_root in settings.roots:
        base = (root / configured_root).resolve()
        if root not in base.parents and base != root:
            raise ValueError(f"source root escapes repository: {configured_root}")
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.is_symlink() or path.suffix not in settings.extensions:
                continue
            relative = path.resolve().relative_to(root).as_posix()
            if not _is_excluded(relative, settings.exclude_globs):
                discovered.add(path.resolve())
    yield from sorted(discovered, key=lambda path: path.relative_to(root).as_posix())


def count_physical_lines(path: Path) -> int:
    """Return the newline-byte count, exactly matching `wc -l` for regular files."""
    return path.read_bytes().count(b"\n")


def check_file_lengths(
    repo_root: Path,
    settings: FileLengthSettings,
    baseline: FileLengthBaseline,
) -> list[FileLengthViolation]:
    """Return all absolute-cap, shrink-ratchet, and stale-baseline failures."""
    root = repo_root.resolve()
    recorded = {entry.path: entry.lines for entry in baseline.entries}
    observed: dict[str, int] = {}
    violations: list[FileLengthViolation] = []
    for path in iter_source_files(root, settings):
        relative = path.relative_to(root).as_posix()
        current = count_physical_lines(path)
        observed[relative] = current
        ceiling = recorded.get(relative)
        if current <= settings.max_lines:
            if ceiling is not None:
                violations.append(
                    FileLengthViolation(
                        path=relative,
                        code="compliant_baseline_entry",
                        current_lines=current,
                        baseline_lines=ceiling,
                        message="file is compliant; remove it from the temporary baseline",
                    )
                )
            continue
        if ceiling is None:
            violations.append(
                FileLengthViolation(
                    path=relative,
                    code="over_limit",
                    current_lines=current,
                    baseline_lines=None,
                    message=f"{current} lines exceeds the {settings.max_lines}-line cap",
                )
            )
        elif current > ceiling:
            violations.append(
                FileLengthViolation(
                    path=relative,
                    code="baseline_growth",
                    current_lines=current,
                    baseline_lines=ceiling,
                    message=f"file grew from its {ceiling}-line baseline to {current} lines",
                )
            )
    for relative, ceiling in recorded.items():
        if relative not in observed:
            violations.append(
                FileLengthViolation(
                    path=relative,
                    code="stale_baseline_entry",
                    current_lines=None,
                    baseline_lines=ceiling,
                    message=(
                        "baseline path is missing, excluded, or no longer a governed source file"
                    ),
                )
            )
    return sorted(violations, key=lambda item: (item.path, item.code))
