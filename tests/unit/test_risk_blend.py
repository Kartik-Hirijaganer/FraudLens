"""Unit tests for the core risk-blend (plan §10.1, §16 Phase 8 "risk-blend in core"): the convex
blend of the model probability + rules subscore, banding at the configured thresholds, the alert
decision, and graceful handling of an empty/garbled threshold map."""

from __future__ import annotations

from decimal import Decimal

import pytest

from fraudlens_core import ModelRiskThresholds, RiskBand, RiskPolicy


def test_blend_is_a_convex_combination() -> None:
    policy = RiskPolicy(model_weight=0.7)
    assert policy.blend(fraud_probability=1.0, rules_subscore=0.0) == pytest.approx(0.7)
    assert policy.blend(fraud_probability=0.0, rules_subscore=1.0) == pytest.approx(0.3)
    assert policy.blend(fraud_probability=1.0, rules_subscore=Decimal("1.0")) == pytest.approx(1.0)


def test_blend_accepts_decimal_subscore() -> None:
    policy = RiskPolicy(model_weight=0.5)
    assert policy.blend(fraud_probability=0.4, rules_subscore=Decimal("0.6")) == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0.0, RiskBand.LOW),
        (0.29, RiskBand.LOW),
        (0.3, RiskBand.MEDIUM),
        (0.59, RiskBand.MEDIUM),
        (0.6, RiskBand.HIGH),
        (0.84, RiskBand.HIGH),
        (0.85, RiskBand.CRITICAL),
        (1.0, RiskBand.CRITICAL),
    ],
)
def test_band_for_default_thresholds(score: float, expected: RiskBand) -> None:
    assert RiskPolicy().band_for(score) is expected


def test_assess_raises_alert_above_threshold() -> None:
    high = RiskPolicy().assess(fraud_probability=0.9, rules_subscore=0.5)
    assert high.combined_score == pytest.approx(0.78)
    assert high.risk_band is RiskBand.HIGH
    assert high.alert is True

    low = RiskPolicy().assess(fraud_probability=0.1, rules_subscore=0.0)
    assert low.risk_band is RiskBand.LOW
    assert low.alert is False


def test_band_for_empty_thresholds_falls_back_to_low() -> None:
    assert RiskPolicy(band_thresholds={}).band_for(0.99) is RiskBand.LOW


def test_custom_thresholds_and_weight() -> None:
    policy = RiskPolicy(
        model_weight=1.0,
        band_thresholds={RiskBand.LOW: 0.0, RiskBand.CRITICAL: 0.5},
        alert_threshold=0.5,
    )
    assessment = policy.assess(fraud_probability=0.6, rules_subscore=0.0)
    assert assessment.combined_score == pytest.approx(0.6)  # model_weight 1.0 ignores rules
    assert assessment.risk_band is RiskBand.CRITICAL
    assert assessment.alert is True


def test_model_risk_identity_without_thresholds_or_weight() -> None:
    policy = RiskPolicy()
    thresholds = ModelRiskThresholds(medium=0.001, high=0.01, critical=0.05)
    assert policy.model_risk(0.42, None) == pytest.approx(0.42)
    assert policy.model_risk(1.7, None) == 1.0  # clamped
    assert policy.model_risk(-0.5, None) == 0.0  # clamped
    rules_only = RiskPolicy(model_weight=0.0)
    assert rules_only.model_risk(0.42, thresholds) == pytest.approx(0.42)


def test_model_risk_maps_operating_points_onto_band_bounds() -> None:
    policy = RiskPolicy()  # weight 0.7, bands 0.3/0.6/0.85
    thresholds = ModelRiskThresholds(medium=0.001, high=0.01, critical=0.05)
    # A model score AT an operating point (zero rules) lands exactly on the band bound.
    at_medium = policy.assess(
        fraud_probability=0.001, rules_subscore=0.0, model_thresholds=thresholds
    )
    assert at_medium.combined_score == pytest.approx(0.3)
    assert at_medium.risk_band is RiskBand.MEDIUM
    at_high = policy.assess(fraud_probability=0.01, rules_subscore=0.0, model_thresholds=thresholds)
    assert at_high.combined_score == pytest.approx(0.6)
    assert at_high.risk_band is RiskBand.HIGH
    assert at_high.alert is True
    below = policy.assess(fraud_probability=0.0002, rules_subscore=0.0, model_thresholds=thresholds)
    assert below.risk_band is RiskBand.LOW
    assert below.alert is False


def test_model_risk_is_monotone_and_bounded() -> None:
    policy = RiskPolicy()
    thresholds = ModelRiskThresholds(medium=0.001, high=0.01, critical=0.05)
    previous = -1.0
    for probability in [0.0, 0.0005, 0.001, 0.004, 0.01, 0.03, 0.05, 0.2, 0.9, 1.0]:
        normalized = policy.model_risk(probability, thresholds)
        assert 0.0 <= normalized <= 1.0
        assert normalized >= previous
        previous = normalized


def test_model_risk_critical_needs_rule_corroboration_at_default_weight() -> None:
    policy = RiskPolicy()
    thresholds = ModelRiskThresholds(medium=0.001, high=0.01, critical=0.05)
    model_only = policy.assess(
        fraud_probability=0.06, rules_subscore=0.0, model_thresholds=thresholds
    )
    assert model_only.risk_band is RiskBand.HIGH  # 0.85/0.7 > 1 -> capped; model alone tops out
    corroborated = policy.assess(
        fraud_probability=0.06, rules_subscore=0.6, model_thresholds=thresholds
    )
    assert corroborated.risk_band is RiskBand.CRITICAL


def test_model_risk_thresholds_require_strict_ordering() -> None:
    with pytest.raises(ValueError, match="medium < high < critical"):
        ModelRiskThresholds(medium=0.01, high=0.01, critical=0.05)
    with pytest.raises(ValueError):
        ModelRiskThresholds(medium=0.5, high=0.4, critical=0.6)
