"""Summary: Recompute published SAR-study aggregates and validate bound quote-level evidence.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- This catches drift between scenario rows, judge evidence, summary means, and frontend projection.
"""

from __future__ import annotations

from pathlib import Path
from statistics import mean, median

import pytest

from lib.sar_eval.publish import validate_published_artifacts

pytestmark = pytest.mark.quality
_REPO_ROOT = Path(__file__).resolve().parents[2]
_REPORT = _REPO_ROOT / "docs" / "reference" / "benchmarks" / "sar-multi-agent-study.json"
_FRONTEND = _REPO_ROOT / "frontend" / "src" / "data" / "sar-multi-agent-study.json"


def test_published_scenario_rows_recompute_summary_means() -> None:
    report = validate_published_artifacts(_REPORT, _FRONTEND)
    field_pairs = (
        ("completeness_rate", "completeness_passed", 5),
        ("unsupported_claims", "unsupported_claim_count", 1),
        ("citation_precision", "citation_precision", 1),
        ("citation_recall", "citation_recall", 1),
        ("fabricated_citation_count", "fabricated_citation_count", 1),
        ("cost_usd", "cost_usd", 1),
        ("latency_ms", "latency_ms", 1),
        ("model_calls", "model_calls", 1),
    )
    summaries = {item.arm: item for item in report.summary.arms}

    for arm_name in ("single_writer", "multi_agent"):
        rows = [getattr(scenario, arm_name) for scenario in report.scenarios]
        summary = summaries[arm_name]
        for summary_field, row_field, divisor in field_pairs:
            observed = mean(getattr(row, row_field) / divisor for row in rows)
            assert getattr(summary, summary_field) == pytest.approx(observed)


def test_quote_evidence_is_complete_and_recomputes_unsupported_medians() -> None:
    report = validate_published_artifacts(_REPORT, _FRONTEND)
    rows = {item.scenario_id: item for item in report.scenarios}
    grouped: dict[tuple[str, str], list[int]] = {}

    for sample in report.judge_samples:
        for arm in sample.arms:
            key = (sample.scenario_id, arm.arm)
            grouped.setdefault(key, []).append(len(arm.unsupported_claims))
            assert all(
                item.quoted_span.strip() and item.reason.strip() for item in arm.unsupported_claims
            )
            assert all(element.present == bool(element.quoted_span) for element in arm.elements)

    for (scenario_id, arm_name), counts in grouped.items():
        assert len(counts) == 3
        assert getattr(rows[scenario_id], arm_name).unsupported_claim_count == median(counts)
