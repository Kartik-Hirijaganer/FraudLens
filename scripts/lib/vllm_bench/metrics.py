"""Summary: Latency, throughput, token-accounting, telemetry, quality, and cost derivation.

Key classes:
- LevelMetrics: all mechanically derived measurements for one arm and concurrency level.

Key functions:
- percentile: linearly interpolated deterministic percentile.
- build_level_metrics: derive one complete level's performance, quality, telemetry, and cost.

Notes:
- Useful throughput counts only drafts passing deterministic quality checks.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from lib.vllm_bench.config import PurchaseOption, QualityConfig
from lib.vllm_bench.quality import QualitySummary, summarize_quality
from lib.vllm_bench.state import BenchmarkCase, LevelCheckpoint
from lib.vllm_bench.telemetry import TelemetrySummary, summarize
from lib.vllm_bench.telemetry import percentile as _percentile

_MODEL_CONFIG = ConfigDict(
    frozen=True, extra="forbid", alias_generator=to_camel, populate_by_name=True
)


class LevelMetrics(BaseModel):
    """Mechanically derived performance, quality, telemetry, and cost for one level."""

    model_config = _MODEL_CONFIG

    concurrency: int = Field(..., gt=0, description="Closed-loop concurrency.")
    requests: int = Field(..., gt=0, description="Measured request count.")
    successful: int = Field(..., ge=0, description="Successful requests.")
    retries: int = Field(..., ge=0, description="Additional attempts.")
    error_rate: float = Field(..., ge=0, le=1, description="Terminal request error rate.")
    latency_p50_ms: float = Field(..., ge=0, description="Median end-to-end latency.")
    latency_p95_ms: float = Field(..., ge=0, description="p95 end-to-end latency.")
    latency_p99_ms: float = Field(..., ge=0, description="p99 end-to-end latency.")
    ttft_p50_ms: float = Field(..., ge=0, description="Median time to first token.")
    ttft_p95_ms: float = Field(..., ge=0, description="p95 time to first token.")
    requests_per_second: float = Field(..., ge=0, description="Completed request throughput.")
    generated_tokens_per_second: float = Field(..., ge=0, description="Output token throughput.")
    useful_drafts_per_second: float = Field(..., ge=0, description="Quality-passing throughput.")
    token_accounting_drift: float = Field(
        ..., ge=0, description="Usage total reconciliation drift."
    )
    duration_seconds: float = Field(..., ge=0, description="Measured wall-clock window.")
    cost_usd: float = Field(..., ge=0, description="Host cost for the level window.")
    cost_per_1000_drafts_usd: float = Field(..., ge=0, description="Normalized draft cost.")
    cost_usd_by_purchase_option: dict[PurchaseOption, float] = Field(
        ..., min_length=1, description="Level cost projected at each configured host rate."
    )
    cost_per_1000_drafts_usd_by_purchase_option: dict[PurchaseOption, float] = Field(
        ..., min_length=1, description="Normalized cost at each configured host rate."
    )
    quality: QualitySummary = Field(..., description="Deterministic output quality.")
    telemetry: TelemetrySummary = Field(..., description="GPU/KV/queue telemetry aggregate.")


def percentile(values: Sequence[float], quantile: float) -> float:
    """Expose the shared deterministic percentile calculation from the metrics surface."""
    return _percentile(values, quantile)


def _token_drift(checkpoint: LevelCheckpoint) -> float:
    """Reconcile server total tokens with prompt plus completion token fields."""
    usages = [item.usage for item in checkpoint.measurements if item.usage is not None]
    reported = sum(item.total_tokens for item in usages)
    components = sum(item.prompt_tokens + item.completion_tokens for item in usages)
    return abs(reported - components) / max(reported, 1)


def build_level_metrics(  # noqa: PLR0913 - pricing context stays explicit and independently testable.
    checkpoint: LevelCheckpoint,
    *,
    cases: Mapping[str, BenchmarkCase],
    hourly_rates_usd: Mapping[PurchaseOption, float],
    purchase_option: PurchaseOption,
    drafts_per_unit: int,
    quality_policy: QualityConfig,
) -> LevelMetrics:
    """Derive one checkpoint's performance, quality, telemetry, token, and cost metrics."""
    success = [item for item in checkpoint.measurements if item.error_code is None]
    latencies = [item.latency_s * 1000 for item in success]
    ttfts = [float(item.ttft_s) * 1000 for item in success if item.ttft_s is not None]
    duration = (checkpoint.completed_at - checkpoint.started_at).total_seconds()
    quality, _details = summarize_quality(cases, checkpoint.measurements, quality_policy)
    completed = len(checkpoint.measurements)
    costs = {
        option: hourly_rate * duration / 3600 for option, hourly_rate in hourly_rates_usd.items()
    }
    if purchase_option not in costs:
        raise ValueError(f"selected purchase option '{purchase_option}' has no configured rate")
    normalized_costs = {
        option: cost / completed * drafts_per_unit for option, cost in costs.items()
    }
    return LevelMetrics(
        concurrency=checkpoint.concurrency,
        requests=completed,
        successful=len(success),
        retries=sum(item.attempts - 1 for item in checkpoint.measurements),
        error_rate=(completed - len(success)) / completed,
        latency_p50_ms=percentile(latencies, 0.50),
        latency_p95_ms=percentile(latencies, 0.95),
        latency_p99_ms=percentile(latencies, 0.99),
        ttft_p50_ms=percentile(ttfts, 0.50),
        ttft_p95_ms=percentile(ttfts, 0.95),
        requests_per_second=completed / duration if duration > 0 else 0,
        generated_tokens_per_second=(
            sum(item.usage.completion_tokens for item in success if item.usage) / duration
            if duration > 0
            else 0
        ),
        useful_drafts_per_second=quality.useful_count / duration if duration > 0 else 0,
        token_accounting_drift=_token_drift(checkpoint),
        duration_seconds=duration,
        cost_usd=costs[purchase_option],
        cost_per_1000_drafts_usd=normalized_costs[purchase_option],
        cost_usd_by_purchase_option=costs,
        cost_per_1000_drafts_usd_by_purchase_option=normalized_costs,
        quality=quality,
        telemetry=summarize(checkpoint.telemetry),
    )
