"""Behavioral tests for canonical project-skill validation and exact Codex mirroring."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

import sync_skills
from lib.skills import compare_skill_trees, sync_skill_trees, validate_skills

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_SKILLS = {
    "adr",
    "azure-experiment",
    "benchmark-vllm",
    "deadcode",
    "design-review",
    "docs",
    "drift-check",
    "k8s-deploy",
    "maintain",
    "pre-pr",
    "quality-gates",
    "split-module",
}


def _write_skill(
    root: Path,
    name: str = "example",
    *,
    frontmatter_name: str | None = None,
    description: str = "Exercise an example project workflow.",
    body: str = "# Example\n\n## When To Use\n\nUse for tests.",
    default_prompt: str = "Use $example with a plan under plans/.",
) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "agents").mkdir()
    (directory / "SKILL.md").write_text(
        f"---\nname: {frontmatter_name or name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    (directory / "agents" / "openai.yaml").write_text(
        f'interface:\n  display_name: "{name.title()}"\n  default_prompt: "{default_prompt}"\n',
        encoding="utf-8",
    )
    return directory


def test_committed_skills_are_valid_exactly_mirrored_and_commands_are_removed() -> None:
    records = validate_skills(REPO_ROOT / ".claude" / "skills")
    assert {record.name for record in records} == EXPECTED_SKILLS
    assert (
        compare_skill_trees(REPO_ROOT / ".claude" / "skills", REPO_ROOT / ".agents" / "skills")
        == []
    )
    assert not (REPO_ROOT / ".claude" / "commands").exists()
    assert not any(record.name.startswith("source-command-") for record in records)


def test_check_fails_on_changed_missing_and_extra_files_then_writer_repairs(
    sandbox: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = sandbox / "canonical"
    mirror = sandbox / "mirror"
    _write_skill(source)
    sync_skill_trees(source, mirror)
    (mirror / "example" / "SKILL.md").write_text("changed\n", encoding="utf-8")
    (mirror / "example" / "agents" / "openai.yaml").unlink()
    (mirror / "obsolete.txt").write_text("extra\n", encoding="utf-8")

    assert sync_skills.main(["--check", "--source", str(source), "--mirror", str(mirror)]) == 1
    output = capsys.readouterr().out
    assert "content" in output
    assert "missing" in output
    assert "extra" in output

    assert sync_skills.main(["--source", str(source), "--mirror", str(mirror)]) == 0
    assert compare_skill_trees(source, mirror) == []
    assert not (mirror / "obsolete.txt").exists()


def test_check_succeeds_and_writer_reports_an_already_synchronized_tree(
    sandbox: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = sandbox / "canonical"
    mirror = sandbox / "mirror"
    _write_skill(source)
    sync_skill_trees(source, mirror)

    assert sync_skills.main(["--check", "--source", str(source), "--mirror", str(mirror)]) == 0
    assert "skills-check OK: 1 project skill(s)" in capsys.readouterr().out
    assert sync_skills.main(["--source", str(source), "--mirror", str(mirror)]) == 0
    assert "already synchronized" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"frontmatter_name": "another"}, "must equal folder"),
        ({"description": "x" * 1025}, "at most 1024"),
        ({"body": "\n".join("line" for _ in range(501))}, "maximum is 500"),
        ({"default_prompt": "Use $example without a plan path."}, "must reference plans/"),
        ({"default_prompt": "Audit .agents/plans/example.md."}, "never .agents/plans/"),
    ],
)
def test_validation_rejects_contract_drift(
    sandbox: Path, changes: dict[str, str], expected: str
) -> None:
    source = sandbox / "canonical"
    _write_skill(source, **changes)
    with pytest.raises((ValueError, ValidationError), match=expected):
        validate_skills(source)


def test_validation_requires_skill_document_and_agent_metadata(sandbox: Path) -> None:
    source = sandbox / "canonical"
    directory = _write_skill(source)
    (directory / "agents" / "openai.yaml").unlink()
    with pytest.raises(ValueError, match="agent metadata is missing"):
        validate_skills(source)

    (directory / "agents" / "openai.yaml").write_text("interface: {}\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="display_name"):
        validate_skills(source)


def test_validation_rejects_invalid_roots_documents_and_blank_interface_text(
    sandbox: Path,
) -> None:
    missing = sandbox / "missing"
    with pytest.raises(FileNotFoundError, match="does not exist"):
        validate_skills(missing)

    empty = sandbox / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="contains no skills"):
        validate_skills(empty)

    loose = sandbox / "loose"
    loose.mkdir()
    (loose / "README.md").write_text("not a skill\n", encoding="utf-8")
    with pytest.raises(ValueError, match="non-directory entry"):
        validate_skills(loose)

    source = sandbox / "canonical"
    directory = _write_skill(source)
    skill_path = directory / "SKILL.md"
    skill_path.unlink()
    with pytest.raises(ValueError, match="skill document is missing"):
        validate_skills(source)

    skill_path.write_text("# Missing frontmatter\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must start"):
        validate_skills(source)

    skill_path.write_text("---\nname: example\ndescription: open ended\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not closed"):
        validate_skills(source)

    _write_skill(sandbox / "blank-source", name="blank")
    metadata = sandbox / "blank-source" / "blank" / "agents" / "openai.yaml"
    metadata.write_text(
        'interface:\n  display_name: " "\n  default_prompt: "Use plans/."\n',
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="cannot be blank"):
        validate_skills(sandbox / "blank-source")


def test_writer_replaces_invalid_mirror_nodes_and_removes_stale_directories(
    sandbox: Path,
) -> None:
    source = sandbox / "canonical"
    mirror_file = sandbox / "mirror-file"
    _write_skill(source)
    mirror_file.write_text("not a directory\n", encoding="utf-8")

    assert {difference.code for difference in compare_skill_trees(source, mirror_file)} == {
        "extra",
        "missing",
    }
    sync_skill_trees(source, mirror_file)
    assert compare_skill_trees(source, mirror_file) == []

    stale = mirror_file / "stale-skill"
    stale.mkdir()
    (stale / "artifact.txt").write_text("obsolete\n", encoding="utf-8")
    expected_destination = mirror_file / "example"
    shutil.rmtree(expected_destination)
    expected_destination.symlink_to(stale, target_is_directory=True)

    sync_skill_trees(source, mirror_file)
    assert compare_skill_trees(source, mirror_file) == []
    assert not stale.exists()


def test_writer_rejects_nested_roots(sandbox: Path) -> None:
    source = sandbox / "canonical"
    _write_skill(source)
    with pytest.raises(ValueError, match="distinct, non-nested"):
        sync_skill_trees(source, source / "mirror")
