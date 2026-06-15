"""Phase 10 unit tests for the pure model-lifecycle helpers (plan §5.4, §9.4, §10.5.1). Verify the
candidate→shadow→approve→canary transition gates, the matured-label eligibility rule, and the
canary auto-abort deviation rule — all pure (no IO), mirroring the alerts state-machine tests."""

from __future__ import annotations

from fraudlens_backend.db.models import ModelVersionStatus
from fraudlens_backend.db.repositories.model_lifecycle import (
    CanaryStats,
    LabelCounts,
    can_approve,
    can_canary,
    can_shadow,
    canary_should_abort,
    labels_eligible,
)


def test_can_shadow_requires_candidate_and_passing_eval() -> None:
    assert can_shadow(ModelVersionStatus.CANDIDATE, has_passing_evaluation=True) is True
    assert can_shadow(ModelVersionStatus.CANDIDATE, has_passing_evaluation=False) is False
    assert can_shadow(ModelVersionStatus.SHADOW, has_passing_evaluation=True) is False


def test_can_approve_only_from_shadow() -> None:
    assert can_approve(ModelVersionStatus.SHADOW) is True
    assert can_approve(ModelVersionStatus.CANDIDATE) is False  # approve blocked pre-eval/shadow
    assert can_approve(ModelVersionStatus.CANARY) is False


def test_can_canary_requires_approved_shadow_or_live_canary() -> None:
    assert can_canary(ModelVersionStatus.SHADOW, approved=True) is True
    assert can_canary(ModelVersionStatus.CANARY, approved=True) is True
    assert can_canary(ModelVersionStatus.SHADOW, approved=False) is False  # unapproved
    assert can_canary(ModelVersionStatus.CANDIDATE, approved=True) is False


def test_labels_eligible_requires_total_and_per_class() -> None:
    eligible = LabelCounts(total=12, positives=6, negatives=6)
    assert labels_eligible(eligible, min_total=10, min_per_class=2) is True
    too_few_total = LabelCounts(total=8, positives=4, negatives=4)
    assert labels_eligible(too_few_total, min_total=10, min_per_class=2) is False
    one_sided = LabelCounts(total=12, positives=11, negatives=1)
    assert labels_eligible(one_sided, min_total=10, min_per_class=2) is False


def test_canary_should_abort_needs_min_samples_and_deviation() -> None:
    # Below the min-sample window: never abort regardless of deviation.
    sparse = CanaryStats(active_count=5, active_mean=0.2, canary_count=5, canary_mean=0.9)
    assert canary_should_abort(sparse, min_samples=20, max_deviation=0.2) is False
    # Enough samples but within tolerance: do not abort.
    stable = CanaryStats(active_count=50, active_mean=0.40, canary_count=50, canary_mean=0.45)
    assert canary_should_abort(stable, min_samples=20, max_deviation=0.2) is False
    # Enough samples and the deviation exceeds the threshold: abort.
    drifting = CanaryStats(active_count=50, active_mean=0.30, canary_count=50, canary_mean=0.80)
    assert canary_should_abort(drifting, min_samples=20, max_deviation=0.2) is True
