"""Summary: Validation and exact-mirror engine for repository-scoped agent skills.
The human-authored .claude/skills tree is the sole source; this module validates
each skill contract and compares or synchronizes the generated .agents/skills tree.

Key classes:
- SkillFrontmatter: validated SKILL.md YAML frontmatter.
- SkillInterface: validated agents/openai.yaml interface metadata.
- AgentMetadata: validated agents/openai.yaml document.
- SkillRecord: one validated canonical skill and its byte-bearing files.
- SkillDifference: one missing, extra, or byte-different mirror artifact.

Key functions:
- validate_skills: validate every canonical skill and return stable records.
- compare_skill_trees: report exact differences without writing.
- sync_skill_trees: make the generated mirror byte-identical to the canonical tree.

Notes:
- Check mode calls only validation and comparison; it never creates or removes paths.
- Symlinks are rejected so a generated mirror cannot escape either managed root.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

SKILL_DOCUMENT = "SKILL.md"
AGENT_METADATA = "agents/openai.yaml"
MAX_DESCRIPTION_CHARS = 1024
MAX_BODY_LINES = 500
_SKILL_NAME_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"


class SkillFrontmatter(BaseModel):
    """Validated YAML frontmatter at the start of one SKILL.md."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(
        ...,
        min_length=1,
        pattern=_SKILL_NAME_PATTERN,
        description="Skill identifier, equal to its canonical folder name.",
    )
    description: str = Field(
        ...,
        min_length=1,
        max_length=MAX_DESCRIPTION_CHARS,
        description="Discovery description shown to supported coding agents.",
    )


class SkillInterface(BaseModel):
    """Agent-facing fields required in agents/openai.yaml."""

    model_config = ConfigDict(extra="allow", frozen=True)

    display_name: str = Field(..., min_length=1, description="Human-readable skill name.")
    short_description: str | None = Field(
        default=None, description="Optional concise skill-picker description."
    )
    default_prompt: str = Field(
        ...,
        min_length=1,
        description="Default invocation prompt referencing the canonical plans directory.",
    )

    @field_validator("display_name", "default_prompt")
    @classmethod
    def _required_text_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("required interface text cannot be blank")
        return value

    @field_validator("default_prompt")
    @classmethod
    def _prompt_never_points_at_the_generated_mirror(cls, value: str) -> str:
        # `.agents/` is a byte-identical copy of `.claude/skills/`, so a prompt that names it
        # would send Codex at the mirror it is already reading instead of the real repository.
        if ".agents/" in value:
            raise ValueError("default_prompt must never reference the generated .agents/ mirror")
        return value


class AgentMetadata(BaseModel):
    """Validated agents/openai.yaml document for one project skill."""

    model_config = ConfigDict(extra="allow", frozen=True)

    interface: SkillInterface = Field(..., description="Skill-picker interface metadata.")


class SkillRecord(BaseModel):
    """One validated canonical skill and all regular files copied to its mirror."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    name: str = Field(..., description="Canonical skill folder name.")
    directory: Path = Field(..., description="Absolute canonical skill directory.")
    files: tuple[Path, ...] = Field(
        ..., min_length=2, description="Relative regular-file paths in stable order."
    )


class SkillDifference(BaseModel):
    """One exact-tree mismatch between canonical source and generated mirror."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(..., description="Path relative to the managed skills root.")
    code: str = Field(..., description="Stable difference type.")
    message: str = Field(..., description="Actionable mismatch explanation.")


def _load_yaml(path: Path) -> Any:
    """Load a YAML document for validation at a Pydantic boundary."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _skill_frontmatter(path: Path) -> tuple[SkillFrontmatter, int]:
    """Parse frontmatter and return it with the first zero-based body-line index."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise ValueError(f"{path}: SKILL.md must start with YAML frontmatter")
    try:
        boundary = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError(f"{path}: SKILL.md frontmatter is not closed") from exc
    frontmatter = SkillFrontmatter.model_validate(yaml.safe_load("\n".join(lines[1:boundary])))
    return frontmatter, boundary + 1


def _regular_files(directory: Path) -> tuple[Path, ...]:
    """Return stable relative regular files, rejecting symlinks anywhere in a skill."""
    files: list[Path] = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"{path}: skill trees may not contain symlinks")
        if path.is_file():
            files.append(path.relative_to(directory))
    return tuple(files)


def validate_skills(source_root: Path) -> tuple[SkillRecord, ...]:
    """Validate all canonical skills and return their file inventories in stable order."""
    root = source_root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"canonical skill root does not exist: {root}")
    loose_files = [path for path in root.iterdir() if not path.is_dir()]
    if loose_files:
        raise ValueError(f"canonical skill root contains non-directory entry: {loose_files[0]}")

    records: list[SkillRecord] = []
    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        if directory.is_symlink():
            raise ValueError(f"{directory}: canonical skill directories may not be symlinks")
        skill_path = directory / SKILL_DOCUMENT
        metadata_path = directory / AGENT_METADATA
        if not skill_path.is_file():
            raise ValueError(f"{skill_path}: required canonical skill document is missing")
        if not metadata_path.is_file():
            raise ValueError(f"{metadata_path}: required agent metadata is missing")

        frontmatter, body_start = _skill_frontmatter(skill_path)
        if frontmatter.name != directory.name:
            raise ValueError(
                f"{skill_path}: frontmatter name {frontmatter.name!r} must equal folder "
                f"{directory.name!r}"
            )
        body_lines = skill_path.read_text(encoding="utf-8").splitlines()[body_start:]
        if len(body_lines) > MAX_BODY_LINES:
            raise ValueError(
                f"{skill_path}: body has {len(body_lines)} lines; maximum is {MAX_BODY_LINES}"
            )
        AgentMetadata.model_validate(_load_yaml(metadata_path))
        records.append(
            SkillRecord(
                name=directory.name,
                directory=directory.resolve(),
                files=_regular_files(directory),
            )
        )
    if not records:
        raise ValueError(f"canonical skill root contains no skills: {root}")
    return tuple(records)


def _expected_files(records: tuple[SkillRecord, ...]) -> dict[str, bytes]:
    """Return the canonical relative-path-to-bytes mapping."""
    return {
        (Path(record.name) / relative).as_posix(): (record.directory / relative).read_bytes()
        for record in records
        for relative in record.files
    }


def _observed_files(mirror_root: Path) -> dict[str, bytes | None]:
    """Return mirror bytes; symlink entries map to None and are therefore rejected."""
    if not mirror_root.exists():
        return {}
    if not mirror_root.is_dir() or mirror_root.is_symlink():
        return {".": None}
    observed: dict[str, bytes | None] = {}
    for path in sorted(mirror_root.rglob("*")):
        relative = path.relative_to(mirror_root).as_posix()
        if path.is_symlink():
            observed[relative] = None
        elif path.is_file():
            observed[relative] = path.read_bytes()
    return observed


def compare_skill_trees(source_root: Path, mirror_root: Path) -> list[SkillDifference]:
    """Return exact byte-level differences without modifying either tree."""
    expected = _expected_files(validate_skills(source_root))
    observed = _observed_files(mirror_root)
    differences: list[SkillDifference] = []
    for relative in sorted(expected.keys() - observed.keys()):
        differences.append(
            SkillDifference(
                path=relative,
                code="missing",
                message="canonical skill file is missing from the generated mirror",
            )
        )
    for relative in sorted(observed.keys() - expected.keys()):
        differences.append(
            SkillDifference(
                path=relative,
                code="extra",
                message="generated mirror contains a file absent from the canonical tree",
            )
        )
    for relative in sorted(expected.keys() & observed.keys()):
        if expected[relative] != observed[relative]:
            differences.append(
                SkillDifference(
                    path=relative,
                    code="content",
                    message="generated mirror bytes differ from the canonical skill",
                )
            )
    return differences


def _validate_distinct_roots(source_root: Path, mirror_root: Path) -> tuple[Path, Path]:
    """Reject equal or nested roots before any writer operation."""
    source = source_root.resolve()
    mirror = mirror_root.resolve()
    if source == mirror or source in mirror.parents or mirror in source.parents:
        raise ValueError("canonical and mirror skill roots must be distinct, non-nested paths")
    return source, mirror


def sync_skill_trees(source_root: Path, mirror_root: Path) -> list[SkillDifference]:
    """Synchronize the generated mirror exactly and return the differences repaired."""
    source, mirror = _validate_distinct_roots(source_root, mirror_root)
    differences = compare_skill_trees(source, mirror)
    expected = _expected_files(validate_skills(source))

    if mirror.exists() and (mirror.is_symlink() or not mirror.is_dir()):
        mirror.unlink()
    mirror.mkdir(parents=True, exist_ok=True)
    expected_skills = {Path(relative).parts[0] for relative in expected}
    for path in tuple(mirror.iterdir()):
        if path.name not in expected_skills:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
    for skill_name in sorted(expected_skills):
        destination = mirror / skill_name
        if destination.is_symlink() or destination.exists():
            if destination.is_symlink() or not destination.is_dir():
                destination.unlink()
                destination.mkdir(parents=True)
        else:
            destination.mkdir(parents=True)
        allowed = {
            Path(relative).relative_to(skill_name)
            for relative in expected
            if Path(relative).parts[0] == skill_name
        }
        for path in sorted(destination.rglob("*"), reverse=True):
            relative_path = path.relative_to(destination)
            if path.is_symlink() or (path.is_file() and relative_path not in allowed):
                path.unlink()
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()

    for relative_name, content in expected.items():
        destination = mirror / relative_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.is_file() or destination.read_bytes() != content:
            destination.write_bytes(content)
    return differences
