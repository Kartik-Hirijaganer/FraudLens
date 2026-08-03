"""Unit tests for the portfolio-demo literal guard (story values restated outside the config)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import check_no_demo_literals
from check_no_demo_literals import forbidden_literals, iter_offences, main
from fraudlens_backend.portfolio_demo import (
    clear_portfolio_demo_config_cache,
    load_portfolio_demo_config,
)
from fraudlens_backend.settings import find_config_dir


def _hits(tmp_path: Path, body: str, *, name: str = "sample.py") -> list[str]:
    """Write body to a temp file and return the forbidden literals the guard finds in it."""
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return [literal for _path, _line, literal in iter_offences(path, forbidden_literals())]


def test_the_forbidden_set_is_derived_from_the_committed_story() -> None:
    config = load_portfolio_demo_config()
    literals = set(forbidden_literals())
    assert str(config.agency.id) in literals
    assert config.agency.slug in literals
    assert config.model.version_label in literals
    assert config.external_id_namespace in literals
    assert {str(p.seed_user_id) for p in config.personas} <= literals
    assert {p.email for p in config.personas} <= literals


def test_the_agency_uuid_is_flagged_anywhere(tmp_path: Path) -> None:
    agency_id = str(load_portfolio_demo_config().agency.id)
    assert _hits(tmp_path, f'AGENCY = uuid.UUID("{agency_id}")\n') == [agency_id]


def test_a_persona_email_is_flagged(tmp_path: Path) -> None:
    email = load_portfolio_demo_config().personas[0].email
    assert _hits(tmp_path, f'user = {{"email": "{email}"}}\n') == [email]


def test_the_model_label_is_flagged_in_any_file_type(tmp_path: Path) -> None:
    label = load_portfolio_demo_config().model.version_label
    assert _hits(tmp_path, f"active model: {label}\n", name="notes.md") == [label]


def test_a_comment_is_no_safer_than_code(tmp_path: Path) -> None:
    """The scan is text-based on purpose: a demo id in a comment is still a second home for it."""
    agency_id = str(load_portfolio_demo_config().agency.id)
    assert _hits(tmp_path, f"# the demo tenant is {agency_id}\n") == [agency_id]


def test_one_finding_per_line_reports_the_longest_match(tmp_path: Path) -> None:
    """A persona email contains the agency slug; the email is the more useful finding."""
    email = load_portfolio_demo_config().personas[0].email
    assert _hits(tmp_path, f'EMAIL = "{email}"\n') == [email]


def test_the_suppression_marker_exempts_a_reviewed_line(tmp_path: Path) -> None:
    agency_id = str(load_portfolio_demo_config().agency.id)
    assert _hits(tmp_path, f'x = "{agency_id}"  # allow-demo-literal\n') == []


def test_a_research_partition_name_is_not_forbidden() -> None:
    """The config declares the agency name as the offline study partition key, so it is shared.

    `scripts/lib/gfp/partitions.py` and the committed GFP artifact legitimately hold that name
    (ADR-017); the exemption is declared in the config, not hand-maintained in the checker.
    """
    config = load_portfolio_demo_config()
    assert config.agency.name == config.agency.research_partition_key
    assert config.agency.name not in forbidden_literals()


def test_a_name_that_is_not_the_partition_key_becomes_forbidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rename the agency without renaming the partition and the name is guarded again."""
    document = yaml.safe_load(
        (find_config_dir() / "portfolio-demo.yaml").read_text(encoding="utf-8")
    )
    document["agency"]["name"] = "Renamed Runtime Tenant"
    target = tmp_path / "portfolio-demo.yaml"
    target.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    clear_portfolio_demo_config_cache()
    renamed = load_portfolio_demo_config(target)
    monkeypatch.setattr(check_no_demo_literals, "load_portfolio_demo_config", lambda: renamed)

    assert "Renamed Runtime Tenant" in forbidden_literals()
    clear_portfolio_demo_config_cache()


def test_main_passes_on_the_current_repo() -> None:
    # Every story value lives only in config/portfolio-demo.yaml (mirrors `make ci`).
    assert main() == 0
