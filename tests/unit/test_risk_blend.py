"""Unit tests for the core risk-blend (plan §10.1, §16 Phase 8 "risk-blend in core"): the convex
blend of the model probability + rules subscore, banding at the configured thresholds, the alert
decision, and graceful handling of an empty/garbled threshold map."""

from __future__ import annotations

from decimal import Decimal

import pytest

from fraudlens_core import RiskBand, RiskPolicy


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
