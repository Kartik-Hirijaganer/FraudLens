"""Unit tests for the pre-registered Tier-3 external-model pilot (release 0.5.0 Phase 3).

The pilot's value is entirely in being fixed BEFORE results are seen, so these tests are about
that property rather than about any measurement: the committed set is exactly the declared size,
every case is unique, both candidates are declared, the tie-break is one of them, and the selection
rule cannot be talked into a candidate that missed a threshold. The last test is the one that
matters most — when nobody qualifies, the rule must return an explicit non-selection rather than
quietly picking the best of a failing field.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from lib.quality.config import load_quality_config
from lib.sar_pilot import (
    SarPilotError,
    SarPilotManifest,
    SarPilotMeasurement,
    load_pilot_manifest,
    select_tier3_model,
)

_GPT5_MINI = "openrouter/openai/gpt-5-mini"
_SONNET = "openrouter/anthropic/claude-sonnet-4.6"
_FROZEN_CASES = 100


def _thresholds():
    """Return the committed SAR acceptance thresholds the pilot is judged against."""
    return load_quality_config().sar_quality


def _measurement(
    model: str, *, cost: str, precision: float = 1.0, **overrides
) -> SarPilotMeasurement:
    """Build one candidate result that clears every threshold unless told otherwise."""
    values = {
        "model": model,
        "cases": _FROZEN_CASES,
        "gate_pass_rate": 0.98,
        "citation_precision": precision,
        "citation_recall": 0.95,
        "required_fact_coverage": 0.97,
        "cost_usd_per_1k_cases": Decimal(cost),
    }
    values.update(overrides)
    return SarPilotMeasurement(**values)


def test_the_committed_manifest_is_frozen_and_internally_consistent() -> None:
    manifest = load_pilot_manifest()

    assert manifest.policy_version == "sar-tier3-pilot-v1"
    assert len(manifest.cases) == _FROZEN_CASES
    assert len(set(manifest.cases)) == _FROZEN_CASES
    assert manifest.composition.total == _FROZEN_CASES
    assert {candidate.model for candidate in manifest.candidates} == {_GPT5_MINI, _SONNET}
    assert manifest.tie_break_model == _GPT5_MINI
    assert manifest.source_run_id == "vllm-bench-f810b57a7b8ae05a"


def test_the_frozen_cases_are_real_escalation_traffic() -> None:
    """Every case is one the gate rejected on a self-hosted tier — what Tier 3 actually sees."""
    manifest = load_pilot_manifest()

    assert manifest.composition.both_tier_failures > 0
    assert manifest.composition.both_tier_failures + manifest.composition.awq_tier_failures == len(
        manifest.cases
    )
    assert manifest.gate_policy_version == "sar-gate-replay-v1"
    assert all(case.startswith("ibm-") for case in manifest.cases)


def test_a_manifest_whose_composition_contradicts_its_cases_is_refused(tmp_path: Path) -> None:
    broken = tmp_path / "pilot.yaml"
    broken.write_text(
        "policy_version: p\nsource_run_id: r\nsource_cases_sha256: d\n"
        "gate_policy_version: g\n"
        "composition: {both_tier_failures: 1, awq_tier_failures: 1, total: 2}\n"
        f"candidates:\n  - model: {_GPT5_MINI}\n  - model: {_SONNET}\n"
        f"tie_break_model: {_GPT5_MINI}\ncases:\n  - only-one-case\n",
        encoding="utf-8",
    )

    with pytest.raises(SarPilotError):
        load_pilot_manifest(broken)


@pytest.mark.parametrize(
    ("composition", "cases", "message"),
    [
        ({"both_tier_failures": 1, "awq_tier_failures": 1, "total": 3}, ["a"], "sum"),
        ({"both_tier_failures": 2, "awq_tier_failures": 0, "total": 2}, ["a", "a"], "unique"),
    ],
)
def test_a_manifest_that_miscounts_or_repeats_its_cases_is_refused(
    composition: dict[str, int], cases: list[str], message: str
) -> None:
    """The frozen set is only fixed if it is also coherent: no bad sum, no repeated case."""
    with pytest.raises(ValueError, match=message):
        SarPilotManifest.model_validate(
            {
                "policy_version": "p",
                "source_run_id": "r",
                "source_cases_sha256": "d",
                "gate_policy_version": "g",
                "composition": composition,
                "candidates": [{"model": _GPT5_MINI}, {"model": _SONNET}],
                "tie_break_model": _GPT5_MINI,
                "cases": cases,
            }
        )


def test_the_cheapest_qualifying_candidate_wins() -> None:
    manifest = load_pilot_manifest()
    results = (_measurement(_GPT5_MINI, cost="0.05"), _measurement(_SONNET, cost="0.42"))

    selection = select_tier3_model(manifest, results, _thresholds())

    assert selection.model == _GPT5_MINI
    assert selection.reason == "cheapest candidate clearing every committed threshold"
    assert selection.qualified == (_GPT5_MINI, _SONNET)
    assert selection.rejected == ()


def test_a_cheaper_candidate_that_misses_a_threshold_does_not_win() -> None:
    """Cost only breaks ties between candidates that already cleared the bar."""
    manifest = load_pilot_manifest()
    results = (
        _measurement(_GPT5_MINI, cost="0.05", precision=0.94),
        _measurement(_SONNET, cost="0.42"),
    )

    selection = select_tier3_model(manifest, results, _thresholds())

    assert selection.model == _SONNET
    assert selection.qualified == (_SONNET,)
    assert selection.rejected == (_GPT5_MINI,)


def test_an_exact_cost_tie_goes_to_the_pre_registered_tie_break_model() -> None:
    manifest = load_pilot_manifest()
    results = (_measurement(_GPT5_MINI, cost="0.20"), _measurement(_SONNET, cost="0.20"))

    selection = select_tier3_model(manifest, results, _thresholds())

    assert selection.model == manifest.tie_break_model
    assert selection.reason == "tie on cost broken by the pre-registered tie-break model"


def test_no_qualifying_candidate_produces_an_explicit_non_selection() -> None:
    """The fallback is an honest two-tier cascade, never a quietly relaxed threshold."""
    manifest = load_pilot_manifest()
    results = (
        _measurement(_GPT5_MINI, cost="0.05", precision=0.90),
        _measurement(_SONNET, cost="0.42", required_fact_coverage=0.5),
    )

    selection = select_tier3_model(manifest, results, _thresholds())

    assert selection.model is None
    assert selection.qualified == ()
    assert set(selection.rejected) == {_GPT5_MINI, _SONNET}
    assert "no candidate cleared" in selection.reason


def test_candidates_run_over_different_case_sets_are_not_comparable() -> None:
    """ "Both candidates run the identical set" is enforced, not assumed."""
    manifest = load_pilot_manifest()
    results = (
        _measurement(_GPT5_MINI, cost="0.05"),
        _measurement(_SONNET, cost="0.42", cases=_FROZEN_CASES - 1),
    )

    with pytest.raises(SarPilotError, match="identical case set"):
        select_tier3_model(manifest, results, _thresholds())


def test_results_that_do_not_cover_the_declared_candidates_are_refused() -> None:
    manifest = load_pilot_manifest()

    with pytest.raises(SarPilotError, match="declared candidates"):
        select_tier3_model(manifest, (_measurement(_GPT5_MINI, cost="0.05"),), _thresholds())


def test_a_manifest_tie_break_outside_its_candidates_is_refused() -> None:
    with pytest.raises(ValueError, match="tie-break"):
        SarPilotManifest.model_validate(
            {
                "policy_version": "p",
                "source_run_id": "r",
                "source_cases_sha256": "d",
                "gate_policy_version": "g",
                "composition": {
                    "both_tier_failures": 1,
                    "awq_tier_failures": 0,
                    "total": 1,
                },
                "candidates": [{"model": _GPT5_MINI}, {"model": _SONNET}],
                "tie_break_model": "openrouter/some/other-model",
                "cases": ["ibm-case-1"],
            }
        )
