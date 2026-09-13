"""Summary: Behavioral tests for reusable deterministic SAR quality metrics.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- Empty denominators are exercised explicitly so benchmark behavior cannot drift silently.
"""

from __future__ import annotations

import pytest

from fraudlens_ml.evaluation import (
    citation_precision_recall,
    false_positive_rate,
    required_fact_coverage,
    unsupported_claim_recall,
)


def test_citation_precision_recall_reports_fabricated_and_missed_ids() -> None:
    metrics = citation_precision_recall(
        ("reg-a", "fabricated"), ("reg-a", "reg-b"), ("reg-a", "reg-b")
    )

    assert metrics.precision == 0.5
    assert metrics.recall == 0.5
    assert metrics.fabricated_ids == ("fabricated",)
    assert metrics.missed_ids == ("reg-b",)


def test_citation_empty_denominators_are_conservative() -> None:
    assert citation_precision_recall((), (), ()).precision == 1.0
    assert citation_precision_recall((), ("reg-a",), ("reg-a",)).precision == 0.0
    assert citation_precision_recall((), (), ()).recall == 1.0


def test_unsupported_recall_and_clean_false_positive_rate() -> None:
    recall = unsupported_claim_recall(("claim-a", "claim-b"), ("claim-a", "noise"))
    false_positives = false_positive_rate((True, True, False), (False, True, True))

    assert recall.rate == 0.5
    assert recall.false_positives == 1
    assert false_positives.rate == 0.5
    assert false_positives.true_positives == 1


def test_false_positive_rate_rejects_misaligned_decisions() -> None:
    with pytest.raises(ValueError, match="equal length"):
        false_positive_rate((True,), ())


def test_required_fact_coverage_normalizes_case_and_whitespace() -> None:
    metrics = required_fact_coverage(
        ("9,500 USD", "HIGH RISK", "missing fact"),
        "The case contains 9,500   USD and was assigned high risk.",
    )

    assert metrics.coverage == pytest.approx(2 / 3)
    assert metrics.missing == ("missing fact",)
