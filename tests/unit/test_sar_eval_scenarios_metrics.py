"""Behavioral tests for the frozen SAR evaluation protocol, scenarios, and BCa metrics."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from fraudlens_backend.portfolio_demo import load_portfolio_demo_config
from fraudlens_backend.settings import find_config_dir
from fraudlens_core import RuleContext
from fraudlens_ml.scoring import DeploymentPointer, ModelCache, ScoreOutput
from lib.sar_eval import scenarios as scenario_module
from lib.sar_eval.config import (
    DEFAULT_SAR_EVAL_CONFIG,
    BootstrapConfig,
    HistoryDirectionMode,
    JudgeConfig,
    SarEvalConfig,
    SarEvalPaths,
    SarTypology,
    ScenarioVariant,
    load_sar_eval_config,
    validate_config_binding,
)
from lib.sar_eval.judge import JudgePromptTemplate
from lib.sar_eval.metrics import bca_mean_interval, pairwise_agreement, pairwise_exact_agreement
from lib.sar_eval.scenarios import (
    SarEvalScenario,
    ScenarioArtifact,
    generate_scenarios,
    load_scenarios,
    validate_alert_preflight,
    write_scenarios,
)


def test_protocol_and_prompt_are_frozen_exact_and_hash_bound() -> None:
    config = load_sar_eval_config()
    prompt = JudgePromptTemplate.load(config.judge.prompt_id)
    prompt_path = find_config_dir() / "llm" / "prompts" / "sar_eval_judge" / "v1.md"

    assert config.typologies == tuple(SarTypology)
    assert config.variants == tuple(ScenarioVariant)
    assert config.judge.samples_per_narrative == 3
    assert config.judge.max_input_bytes == 32_768
    assert config.bootstrap.resamples == 10_000
    assert config.calibration.minimum_combined_score == 0.6
    assert config.calibration.model_version == load_portfolio_demo_config().model.version_label
    assert set(config.calibration.typologies) == set(SarTypology)
    assert config.config_sha256 == hashlib.sha256(DEFAULT_SAR_EVAL_CONFIG.read_bytes()).hexdigest()
    assert prompt.prompt_version == "v1@1.0.0"
    assert prompt.prompt_hash == hashlib.sha256(prompt_path.read_bytes()).hexdigest()
    with pytest.raises(ValidationError, match="frozen"):
        config.seed = 1  # type: ignore[misc]
    with pytest.raises(ValueError, match="does not match"):
        validate_config_binding(config, "0" * 64)


def test_protocol_rejects_matrix_drift_and_non_scratch_output() -> None:
    raw = load_sar_eval_config().model_dump(mode="json")
    raw["typologies"] = raw["typologies"][:-1]
    with pytest.raises(ValidationError, match="every SarTypology"):
        SarEvalConfig.model_validate(raw)

    raw = load_sar_eval_config().model_dump(mode="json")
    raw["paths"]["output_dir"] = "artifacts/sar-eval"
    with pytest.raises(ValidationError, match=r"under \.local"):
        SarEvalConfig.model_validate(raw)

    raw = load_sar_eval_config().model_dump(mode="json")
    raw["variants"] = list(reversed(raw["variants"]))
    with pytest.raises(ValidationError, match="every ScenarioVariant"):
        SarEvalConfig.model_validate(raw)

    raw = load_sar_eval_config().model_dump(mode="json")
    del raw["calibration"]["typologies"][SarTypology.STRUCTURING]
    with pytest.raises(ValidationError, match="every SarTypology"):
        SarEvalConfig.model_validate(raw)

    raw = load_sar_eval_config().model_dump(mode="json")
    raw["calibration"]["minimum_combined_score"] = 0.5
    with pytest.raises(ValidationError, match=r"exactly 0\.6"):
        SarEvalConfig.model_validate(raw)


def test_nested_protocol_pins_paths_and_yaml_boundary_are_strict(tmp_path: Path) -> None:
    judge = load_sar_eval_config().judge.model_dump()
    judge["samples_per_narrative"] = 2
    with pytest.raises(ValidationError, match="exactly 3"):
        JudgeConfig.model_validate(judge)

    with pytest.raises(ValidationError, match="exactly 10000"):
        BootstrapConfig(resamples=9999, confidence_level=0.95)
    with pytest.raises(ValidationError, match=r"exactly 0\.95"):
        BootstrapConfig(resamples=10_000, confidence_level=0.9)
    with pytest.raises(ValidationError, match="without traversal"):
        SarEvalPaths(output_dir=".local/sar-eval", corpus_dir="../private")

    scalar = tmp_path / "scalar.yaml"
    scalar.write_text("not-a-mapping\n", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML mapping"):
        load_sar_eval_config(scalar)

    raw = load_sar_eval_config().model_dump(mode="json")
    raw["api"]["loopback_http_hosts"] = ["localhost", "192.0.2.1"]
    with pytest.raises(ValidationError, match="only loopback hosts"):
        SarEvalConfig.model_validate(raw)


def test_scenario_matrix_is_deterministic_unique_synthetic_and_round_trips(tmp_path: Path) -> None:
    config_bytes = DEFAULT_SAR_EVAL_CONFIG.read_bytes()
    config = load_sar_eval_config()
    first = generate_scenarios(config, config_bytes)
    second = generate_scenarios(load_sar_eval_config(), config_bytes)

    with pytest.raises(ValueError, match="config SHA-256"):
        generate_scenarios(config, b"tampered protocol bytes")

    assert first == second
    assert len(first.scenarios) == 32
    assert len({item.scenario_id for item in first.scenarios}) == 32
    assert {(item.typology, item.variant) for item in first.scenarios} == {
        (typology, variant) for typology in SarTypology for variant in ScenarioVariant
    }
    assert all(
        item.transactions[-1].external_id == item.subject_external_id for item in first.scenarios
    )
    assert all(
        transaction.origin_account.startswith("SYNTH-")
        or transaction.dest_account.startswith("SYNTH-")
        for item in first.scenarios
        for transaction in item.transactions
    )
    assert all(
        transaction.external_id.startswith(f"{first.run_id}-")
        and len(transaction.external_id) <= 128
        for scenario in first.scenarios
        for transaction in scenario.transactions
    )
    changed_seed_config = config.model_copy(update={"seed": config.seed + 1})
    changed_run = generate_scenarios(changed_seed_config, config_bytes)
    assert changed_run.run_id != first.run_id
    assert {
        transaction.external_id
        for scenario in first.scenarios
        for transaction in scenario.transactions
    }.isdisjoint(
        {
            transaction.external_id
            for scenario in changed_run.scenarios
            for transaction in scenario.transactions
        }
    )
    citation_bait = next(
        item for item in first.scenarios if item.variant is ScenarioVariant.CITATION_BAIT
    )
    assert "31 CFR 9999.999" in citation_bait.transactions[-1].channel

    target = tmp_path / first.run_id / "scenarios.json"
    write_scenarios(target, first)
    assert load_scenarios(target) == first
    assert "scenarioId" in target.read_text(encoding="utf-8")

    renamed = first.model_copy(update={"run_id": "sar-eval-renamed"})
    wrong_target = tmp_path / renamed.run_id / "scenarios.json"
    write_scenarios(wrong_target, renamed)
    with pytest.raises(ValueError, match="not canonical"):
        load_scenarios(wrong_target)


def test_scenario_alert_preflight_uses_pinned_model_and_rejects_uncalibrated_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_sar_eval_config()
    artifact = generate_scenarios(config, DEFAULT_SAR_EVAL_CONFIG.read_bytes())

    class DeterministicScorer:
        def __init__(self, _cache: ModelCache) -> None:
            pass

        def score(self, pointer: DeploymentPointer, context: RuleContext) -> ScoreOutput:
            assert pointer.active_version_label == config.calibration.model_version
            probability = 0.0 if context.transaction.amount == Decimal("1") else 1.0
            return ScoreOutput(
                fraud_probability=probability,
                model_version_label=pointer.active_version_label,
            )

    monkeypatch.setattr(scenario_module, "Scorer", DeterministicScorer)
    validate_alert_preflight(artifact, config)

    scenario = artifact.scenarios[-1]
    subject = scenario.transactions[-1].model_copy(
        update={"amount": Decimal("1"), "channel": "card", "country": "US"}
    )
    weakened = scenario.model_copy(update={"transactions": (*scenario.transactions[:-1], subject)})
    drifted = artifact.model_copy(update={"scenarios": (*artifact.scenarios[:-1], weakened)})
    with pytest.raises(ValueError, match="alert preflight failed"):
        validate_alert_preflight(drifted, config)


def test_each_typology_has_a_distinct_api_visible_transaction_pattern() -> None:
    artifact = generate_scenarios(load_sar_eval_config(), DEFAULT_SAR_EVAL_CONFIG.read_bytes())
    clean = {
        item.typology: item for item in artifact.scenarios if item.variant is ScenarioVariant.CLEAN
    }

    structuring = clean[SarTypology.STRUCTURING]
    assert all(item.amount < Decimal("10000") for item in structuring.transactions)
    assert all(item.channel == "cash_deposit" for item in structuring.transactions)
    assert all(item.dest_account.startswith("SYNTH-HUB") for item in structuring.transactions)

    high_risk = clean[SarTypology.HIGH_RISK_WIRE]
    assert high_risk.transactions[-1].country == "IR"
    assert high_risk.transactions[-1].amount == Decimal("48000")
    assert all(item.channel == "international_wire" for item in high_risk.transactions)

    rapid = clean[SarTypology.RAPID_MOVEMENT]
    assert rapid.transactions[-1].amount == Decimal("14900")
    rapid_gap = rapid.transactions[1].occurred_at - rapid.transactions[0].occurred_at
    assert rapid_gap.total_seconds() == pytest.approx(43_200)
    assert rapid.transactions[0].dest_account.startswith("SYNTH-HUB")
    assert rapid.transactions[1].origin_account.startswith("SYNTH-HUB")

    funnel = clean[SarTypology.FUNNEL_ACCOUNT]
    assert all(item.dest_account.startswith("SYNTH-HUB") for item in funnel.transactions[:-1])
    assert funnel.transactions[-1].origin_account.startswith("SYNTH-HUB")
    assert funnel.transactions[-1].channel == "outbound_wire"

    mule = clean[SarTypology.MULE_VELOCITY]
    mule_gap = mule.transactions[1].occurred_at - mule.transactions[0].occurred_at
    assert mule_gap.total_seconds() == pytest.approx(3600)
    assert all(item.channel == "peer_to_peer" for item in mule.transactions)
    assert mule.transactions[-1].country == "GB"

    layering = clean[SarTypology.ROUND_AMOUNT_LAYERING]
    assert all(item.amount % Decimal("1000") == 0 for item in layering.transactions)
    assert layering.transactions[-1].amount == Decimal("50000")
    assert layering.transactions[-1].country == "KY"

    crypto = clean[SarTypology.CRYPTO_OFF_RAMP]
    assert all(item.channel == "crypto_exchange" for item in crypto.transactions)
    assert crypto.transactions[-1].country == "SV"
    assert crypto.transactions[-1].dest_account.startswith("SYNTH-HUB")

    shell = clean[SarTypology.SHELL_COMPANY_TRANSFER]
    assert min(item.amount for item in shell.transactions) >= Decimal("75000")
    assert shell.transactions[-1].amount == Decimal("1000000")
    assert shell.transactions[-1].country == "PA"

    signatures = {
        tuple(
            (
                str(item.amount),
                item.channel,
                item.country,
                item.origin_account.startswith("SYNTH-HUB"),
            )
            for item in scenario.transactions
        )
        for scenario in clean.values()
    }
    assert len(signatures) == len(SarTypology)

    assert (
        load_sar_eval_config()
        .calibration.typologies[SarTypology.HIGH_RISK_WIRE]
        .history_direction_mode
        is HistoryDirectionMode.ALL_INBOUND
    )


def test_scenario_artifact_rejects_duplicate_or_incomplete_matrix() -> None:
    artifact = generate_scenarios(load_sar_eval_config(), DEFAULT_SAR_EVAL_CONFIG.read_bytes())
    raw = artifact.model_dump(mode="json", by_alias=True)
    raw["scenarios"][-1] = raw["scenarios"][0]

    with pytest.raises(ValidationError, match="typology x variant"):
        ScenarioArtifact.model_validate(raw)

    raw = artifact.model_dump(mode="json", by_alias=True)
    raw["scenarios"][-1]["scenarioId"] = raw["scenarios"][0]["scenarioId"]
    with pytest.raises(ValidationError, match="scenario ids"):
        ScenarioArtifact.model_validate(raw)


def test_scenario_rejects_duplicate_transactions_and_subject_not_last() -> None:
    scenario = _artifact = generate_scenarios(
        load_sar_eval_config(), DEFAULT_SAR_EVAL_CONFIG.read_bytes()
    ).scenarios[0]
    raw = scenario.model_dump(mode="json", by_alias=True)
    raw["transactions"][1] = raw["transactions"][0]
    with pytest.raises(ValidationError, match="external ids"):
        SarEvalScenario.model_validate(raw)

    raw = _artifact.model_dump(mode="json", by_alias=True)
    raw["subjectExternalId"] = raw["transactions"][0]["externalId"]
    with pytest.raises(ValidationError, match="subject must be the final"):
        SarEvalScenario.model_validate(raw)


def test_bca_is_paired_fixed_seed_and_handles_constant_deltas() -> None:
    values = np.array([-0.4, -0.1, 0.2, 0.5, 0.8], dtype=np.float64)
    first = bca_mean_interval(values, resamples=10_000, confidence_level=0.95, seed=271828)
    second = bca_mean_interval(values, resamples=10_000, confidence_level=0.95, seed=271828)
    constant = bca_mean_interval(
        np.full(32, 0.25), resamples=10_000, confidence_level=0.95, seed=271828
    )

    assert first == second
    assert first.lower <= first.point_estimate <= first.upper
    assert constant.point_estimate == pytest.approx(0.25)
    assert constant.lower == pytest.approx(0.25)
    assert constant.upper == pytest.approx(0.25)


def test_bca_and_agreement_reject_invalid_inputs_and_compute_pairwise_rate() -> None:
    with pytest.raises(ValueError, match="at least two"):
        bca_mean_interval(np.array([1.0]), resamples=10_000, confidence_level=0.95, seed=1)
    with pytest.raises(ValueError, match="exactly three"):
        pairwise_agreement(((True,), (True,)))

    observed = pairwise_agreement(((True, False), (True, True), (False, True)))
    assert observed == pytest.approx(1 / 3)
    assert pairwise_exact_agreement((1, 1, 2)) == pytest.approx(1 / 3)
    assert pairwise_exact_agreement(
        (frozenset({"one"}), frozenset({"one"}), frozenset({"two"}))
    ) == pytest.approx(1 / 3)
    with pytest.raises(ValueError, match="exactly three"):
        pairwise_exact_agreement((1, 1))
