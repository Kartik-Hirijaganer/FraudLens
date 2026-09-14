"""Summary: Benchmark percentile, throughput, token-drift, telemetry, and cost tests.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- Fixed timestamps make every derived quantity exact and reproducible.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from vllm_bench_fakes import HASH, NOW, benchmark_case, measurement, telemetry

from lib.vllm_bench.config import load_config
from lib.vllm_bench.metrics import build_level_metrics, percentile
from lib.vllm_bench.state import LevelCheckpoint, TokenUsage


def test_percentile_interpolates_and_ignores_non_finite_values() -> None:
    assert percentile((1, 2, 3), 0.5) == 2
    assert percentile((1, 3), 0.5) == 2
    assert percentile((float("nan"), float("inf")), 0.95) == 0
    with pytest.raises(ValueError, match="between zero and one"):
        percentile((1,), 1.1)


def test_level_metrics_derive_performance_retries_cost_and_token_drift() -> None:
    first = benchmark_case("case-0")
    second = benchmark_case("case-1")
    measurements = (
        measurement(first, 0, latency_s=1, attempts=2),
        measurement(second, 1, latency_s=3).model_copy(
            update={"usage": TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=25)}
        ),
    )
    checkpoint = LevelCheckpoint(
        arm="bf16",
        concurrency=2,
        case_order_sha256=HASH,
        cases_sha256=HASH,
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=4),
        warmup_completed=1,
        measurements=measurements,
        telemetry=(telemetry(),),
    )
    metrics = build_level_metrics(
        checkpoint,
        cases={first.case_id: first, second.case_id: second},
        hourly_rates_usd={"pay_as_you_go": 3.6, "spot": 1.8},
        purchase_option="pay_as_you_go",
        drafts_per_unit=1000,
        quality_policy=load_config().quality,
    )
    assert metrics.requests == 2
    assert metrics.successful == 2
    assert metrics.retries == 1
    assert metrics.latency_p50_ms == 2000
    assert metrics.requests_per_second == 0.5
    assert metrics.generated_tokens_per_second == 5
    assert metrics.token_accounting_drift == pytest.approx(5 / 45)
    assert metrics.cost_usd == pytest.approx(0.004)
    assert metrics.cost_per_1000_drafts_usd == pytest.approx(2)
    assert metrics.cost_usd_by_purchase_option["spot"] == pytest.approx(0.002)
    assert metrics.cost_per_1000_drafts_usd_by_purchase_option["spot"] == pytest.approx(1)
    assert metrics.telemetry.samples == 1


def test_zero_duration_and_terminal_error_are_safe() -> None:
    case = benchmark_case()
    checkpoint = LevelCheckpoint(
        arm="bf16",
        concurrency=1,
        case_order_sha256=HASH,
        cases_sha256=HASH,
        started_at=NOW,
        completed_at=NOW,
        warmup_completed=0,
        measurements=(measurement(case, 0, error_code="http_error"),),
    )
    metrics = build_level_metrics(
        checkpoint,
        cases={case.case_id: case},
        hourly_rates_usd={"pay_as_you_go": 1},
        purchase_option="pay_as_you_go",
        drafts_per_unit=1000,
        quality_policy=load_config().quality,
    )
    assert metrics.error_rate == 1
    assert metrics.requests_per_second == 0
    assert metrics.generated_tokens_per_second == 0
    assert metrics.useful_drafts_per_second == 0
