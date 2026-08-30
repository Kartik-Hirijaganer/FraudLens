"""Validation tests for the portfolio demo story config loader.

Invalid cases are built by MUTATING the committed document, never by duplicating it, so a
change to the real story cannot leave these fixtures quietly describing something else.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from fraudlens_backend.db.models.enums import AlertStatus, SarStatus, UserRole
from fraudlens_backend.portfolio_demo import (
    AUDIT_ACTION,
    PortfolioDemoConfigError,
    clear_portfolio_demo_config_cache,
    load_portfolio_demo_config,
)
from fraudlens_backend.portfolio_demo.config import _resolve_config_path
from fraudlens_backend.settings import AppSettings, find_config_dir
from fraudlens_core import RiskBand

_COMMITTED = find_config_dir() / "portfolio-demo.yaml"


@pytest.fixture(autouse=True)
def _isolated_cache() -> Any:
    """Never let one test's document leak into another through the process cache."""
    clear_portfolio_demo_config_cache()
    yield
    clear_portfolio_demo_config_cache()


def _document() -> dict[str, Any]:
    """Return a fresh mutable copy of the committed story document."""
    return yaml.safe_load(_COMMITTED.read_text(encoding="utf-8"))


def _write(tmp_path: Path, document: dict[str, Any]) -> Path:
    """Persist a mutated document and return its path."""
    target = tmp_path / "portfolio-demo.yaml"
    target.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return target


def _load(tmp_path: Path, document: dict[str, Any]) -> Any:
    """Load a mutated document through the real loader."""
    clear_portfolio_demo_config_cache()
    return load_portfolio_demo_config(_write(tmp_path, document))


def _scenario(**overrides: Any) -> dict[str, Any]:
    """Return a minimal valid scenario mapping the tests can bend."""
    scenario: dict[str, Any] = {
        "scenario_id": "s1",
        "external_id_suffix": "001",
        "transaction": {
            "amount": "9100.00",
            "currency": "USD",
            "origin_account": "SYNTH-ORIGIN-0001",
            "dest_account": "SYNTH-DEST-0002",
            "channel": "wire",
            "country": "US",
            "occurred_offset_hours": -4.0,
        },
        "score": True,
        "expected_risk_band": "high",
        "expected_triggered_rules": ["structuring"],
        "alert_target": "open",
        "sar_target": "draft",
    }
    scenario.update(overrides)
    return scenario


def _with_one_scenario(**overrides: Any) -> dict[str, Any]:
    """Return the committed document carrying exactly one consistent scenario."""
    document = _document()
    scenario = _scenario(**overrides)
    document["scenarios"] = [scenario]
    document["execution"]["mock_agent_revision_scenario"] = scenario["scenario_id"]
    band = scenario.get("expected_risk_band")
    document["expected"] = {
        "transactions": 1,
        "unscored": 0 if scenario["score"] else 1,
        "risk_bands": {band: 1} if band else {},
        "alert_states": {scenario["alert_target"]: 1} if scenario.get("alert_target") else {},
        "sar_states": {scenario["sar_target"]: 1} if scenario.get("sar_target") else {},
    }
    return document


# --- the committed document -----------------------------------------------------------------


def test_committed_story_loads_and_exposes_derived_identity() -> None:
    config = load_portfolio_demo_config()
    assert config.story_identity.startswith(config.external_id_namespace)
    assert config.story_version in config.story_identity
    assert config.audit_request_id == f"{AUDIT_ACTION}:{config.story_identity}"
    # A Postgres advisory lock key is a signed 64-bit integer.
    assert -(2**63) <= config.advisory_lock_key < 2**63
    assert config.story_anchor.tzinfo is not None


def test_every_role_resolves_to_exactly_one_persona() -> None:
    config = load_portfolio_demo_config()
    for role in UserRole:
        persona = config.persona_for_role(role)
        assert persona is not None
        assert config.persona(persona.key) is persona
    with pytest.raises(PortfolioDemoConfigError):
        config.persona("not-a-persona")


def test_the_process_cache_is_reused_and_clearable() -> None:
    first = load_portfolio_demo_config()
    assert load_portfolio_demo_config() is first
    clear_portfolio_demo_config_cache()
    assert load_portfolio_demo_config() is not first


def test_settings_may_be_supplied_explicitly() -> None:
    settings = AppSettings(environment="dev")
    assert load_portfolio_demo_config(settings=settings).agency.slug


# --- derived ids ------------------------------------------------------------------------------


def test_external_ids_and_timestamps_derive_from_the_story(tmp_path: Path) -> None:
    config = _load(tmp_path, _with_one_scenario())
    scenario = config.scenarios[0]
    assert config.external_id(scenario) == f"{config.story_identity}-001"
    assert config.occurred_at(scenario) < config.story_anchor
    assert config.scored_scenarios == (scenario,)


# --- path safety --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename",
    ["", "/etc/passwd", "../outside.yaml", "~/elsewhere.yaml", "absent.yaml"],
)
def test_unsafe_or_missing_config_paths_are_refused(filename: str) -> None:
    with pytest.raises(PortfolioDemoConfigError):
        _resolve_config_path(find_config_dir(), filename)


def test_a_symlink_escaping_the_config_dir_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside.yaml"
    outside.write_text("{}", encoding="utf-8")
    link = find_config_dir() / "portfolio-demo-escape.yaml"
    link.symlink_to(outside)
    try:
        with pytest.raises(PortfolioDemoConfigError, match="outside the config directory"):
            _resolve_config_path(find_config_dir(), link.name)
    finally:
        link.unlink()


def test_the_settings_filename_is_resolved_under_the_config_dir(tmp_path: Path) -> None:
    settings = AppSettings(portfolio_demo_config_file="portfolio-demo.yaml")
    assert load_portfolio_demo_config(settings=settings).agency.id
    missing = AppSettings(portfolio_demo_config_file="does-not-exist.yaml")
    with pytest.raises(PortfolioDemoConfigError, match="is missing"):
        load_portfolio_demo_config(settings=missing)
    del tmp_path


def test_an_explicit_path_must_be_a_file(tmp_path: Path) -> None:
    with pytest.raises(PortfolioDemoConfigError, match="not a file"):
        load_portfolio_demo_config(tmp_path)


def test_a_non_mapping_or_unparsable_document_is_refused(tmp_path: Path) -> None:
    listy = tmp_path / "listy.yaml"
    listy.write_text("- one\n- two\n", encoding="utf-8")
    with pytest.raises(PortfolioDemoConfigError, match="YAML mapping"):
        load_portfolio_demo_config(listy)
    broken = tmp_path / "broken.yaml"
    broken.write_text("key: [unterminated\n", encoding="utf-8")
    with pytest.raises(PortfolioDemoConfigError, match="could not be read"):
        load_portfolio_demo_config(broken)


# --- document validation ------------------------------------------------------------------------


def test_an_unknown_key_is_refused(tmp_path: Path) -> None:
    document = _document()
    document["surprise"] = True
    with pytest.raises(PortfolioDemoConfigError, match="extra_forbidden"):
        _load(tmp_path, document)


def test_validation_errors_never_echo_the_offending_value(tmp_path: Path) -> None:
    document = _document()
    document["agency"]["id"] = "SECRET-LOOKING-VALUE"
    with pytest.raises(PortfolioDemoConfigError) as raised:
        _load(tmp_path, document)
    assert "SECRET-LOOKING-VALUE" not in str(raised.value)
    assert "agency.id" in str(raised.value)


def test_a_naive_story_anchor_is_refused(tmp_path: Path) -> None:
    document = _document()
    document["story_anchor"] = "2026-07-01 09:00:00"
    with pytest.raises(PortfolioDemoConfigError, match="timezone-aware"):
        _load(tmp_path, document)


@pytest.mark.parametrize("field", ["key", "seed_user_id", "email"])
def test_duplicate_persona_identities_are_refused(tmp_path: Path, field: str) -> None:
    document = _document()
    clone = dict(document["personas"][1])
    # Collide with a DIFFERENT persona's real value, read from the document rather than restated.
    clone[field] = document["personas"][0][field]
    document["personas"].append(clone)
    with pytest.raises(PortfolioDemoConfigError, match="must be unique"):
        _load(tmp_path, document)


@pytest.mark.parametrize("field", ["scenario_id", "external_id_suffix"])
def test_duplicate_scenario_identities_are_refused(tmp_path: Path, field: str) -> None:
    document = _with_one_scenario()
    twin = _scenario(scenario_id="s2", external_id_suffix="002")
    twin[field] = document["scenarios"][0][field]
    document["scenarios"].append(twin)
    document["expected"]["transactions"] = 2
    document["expected"]["risk_bands"] = {"high": 2}
    document["expected"]["alert_states"] = {"open": 2}
    document["expected"]["sar_states"] = {"draft": 2}
    with pytest.raises(PortfolioDemoConfigError, match="must be unique"):
        _load(tmp_path, document)


def test_an_unresolved_workflow_actor_is_refused(tmp_path: Path) -> None:
    document = _document()
    document["workflow"]["assignee"] = "nobody"
    with pytest.raises(PortfolioDemoConfigError, match="does not resolve"):
        _load(tmp_path, document)


def test_an_actor_whose_role_lacks_the_permission_is_refused(tmp_path: Path) -> None:
    document = _document()
    document["workflow"]["sar_review_actor"] = "auditor"
    with pytest.raises(PortfolioDemoConfigError, match="does not grant 'review_sar'"):
        _load(tmp_path, document)


def test_an_unknown_default_bypass_persona_is_refused(tmp_path: Path) -> None:
    document = _document()
    document["default_bypass_persona"] = "ghost"
    with pytest.raises(PortfolioDemoConfigError, match="default_bypass_persona"):
        _load(tmp_path, document)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("transactions", 7),
        ("unscored", 3),
        ("risk_bands", {"low": 4}),
        ("alert_states", {"open": 4}),
        ("sar_states", {"draft": 4}),
    ],
)
def test_every_count_must_be_the_algebraic_consequence_of_the_scenarios(
    tmp_path: Path, key: str, value: Any
) -> None:
    document = _with_one_scenario()
    document["expected"][key] = value
    with pytest.raises(PortfolioDemoConfigError, match=r"scenarios imply|is \d+"):
        _load(tmp_path, document)


def test_a_negative_distribution_count_is_refused(tmp_path: Path) -> None:
    document = _document()
    document["expected"]["risk_bands"]["low"] = -1
    with pytest.raises(PortfolioDemoConfigError, match="must not be negative"):
        _load(tmp_path, document)


def test_a_scored_scenario_must_pin_a_band(tmp_path: Path) -> None:
    document = _with_one_scenario()
    document["scenarios"][0]["expected_risk_band"] = None
    with pytest.raises(PortfolioDemoConfigError, match="needs expected band"):
        _load(tmp_path, document)


def test_an_unscored_scenario_must_pin_no_outcome(tmp_path: Path) -> None:
    document = _with_one_scenario(score=False)
    document["scenarios"][0]["expected_risk_band"] = "low"
    with pytest.raises(PortfolioDemoConfigError, match="must pin no scored outcome"):
        _load(tmp_path, document)


def test_a_held_unscored_scenario_is_accepted(tmp_path: Path) -> None:
    document = _with_one_scenario(
        score=False,
        expected_risk_band=None,
        expected_triggered_rules=[],
        alert_target=None,
        sar_target=None,
    )
    config = _load(tmp_path, document)
    assert config.expected.unscored == 1
    assert config.scored_scenarios == ()


def test_alert_and_sar_targets_must_be_set_together(tmp_path: Path) -> None:
    document = _with_one_scenario(sar_target=None)
    document["expected"]["sar_states"] = {}
    with pytest.raises(PortfolioDemoConfigError, match="exactly one SAR draft"):
        _load(tmp_path, document)


def test_a_band_below_the_alert_threshold_cannot_carry_targets(tmp_path: Path) -> None:
    document = _with_one_scenario(expected_risk_band="low", expected_triggered_rules=[])
    with pytest.raises(PortfolioDemoConfigError, match="does not reach the alert threshold"):
        _load(tmp_path, document)


def test_an_unknown_rule_code_is_refused(tmp_path: Path) -> None:
    document = _with_one_scenario(expected_triggered_rules=["not_a_rule"])
    with pytest.raises(PortfolioDemoConfigError, match="unknown rule codes"):
        _load(tmp_path, document)


def test_duplicate_rule_codes_are_refused(tmp_path: Path) -> None:
    document = _with_one_scenario(expected_triggered_rules=["structuring", "structuring"])
    with pytest.raises(PortfolioDemoConfigError, match="duplicates"):
        _load(tmp_path, document)


def test_accounts_that_mask_identically_are_refused(tmp_path: Path) -> None:
    # Same length, same last four -> the same masked value -> merged history windows.
    document = _with_one_scenario()
    document["scenarios"][0]["transaction"]["dest_account"] = "SYNTH-DESTXX-0001"
    document["scenarios"][0]["transaction"]["origin_account"] = "SYNTH-ORIGIN-0001"
    with pytest.raises(PortfolioDemoConfigError, match="mask to the same value"):
        _load(tmp_path, document)


@pytest.mark.parametrize("weights", [[0, 1], [3]])
def test_case_pack_weights_must_match_the_partition_count(
    tmp_path: Path, weights: list[int]
) -> None:
    document = _document()
    document["case_pack_tenant_weights"] = weights
    with pytest.raises(PortfolioDemoConfigError, match="case_pack_tenant_weights"):
        _load(tmp_path, document)


def test_the_probe_margin_must_match_the_settings_review_window(tmp_path: Path) -> None:
    document = _document()
    document["probe"]["low_confidence_margin"] = 0.25
    with pytest.raises(PortfolioDemoConfigError, match="review_low_confidence_margin"):
        _load(tmp_path, document)


def test_an_inline_password_value_is_refused_by_the_env_pointer_shape(tmp_path: Path) -> None:
    document = _document()
    document["auth"]["public_synthetic_password_env"] = "demo-access-2026"
    with pytest.raises(PortfolioDemoConfigError, match="public_synthetic_password_env"):
        _load(tmp_path, document)


def test_config_dir_override_is_honoured(tmp_path: Path, monkeypatch: Any) -> None:
    (tmp_path / "default.yaml").write_text("app_name: FraudLens\n", encoding="utf-8")
    _write(tmp_path, _document())
    monkeypatch.setitem(os.environ, "FRAUDLENS_CONFIG_DIR", str(tmp_path))
    clear_portfolio_demo_config_cache()
    config = load_portfolio_demo_config(settings=AppSettings())
    assert config.agency.slug
    assert set(config.expected.risk_bands) <= set(RiskBand)
    assert set(config.expected.alert_states) <= set(AlertStatus)
    assert set(config.expected.sar_states) <= set(SarStatus)
