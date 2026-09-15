"""Summary: Strict publishable report contracts and mechanical benchmark headline derivation.

Key classes:
- AcceptanceCheck: one data-derived publication criterion.
- QualityDelta: AWQ-minus-BF16 percentage-point quality comparison.
- ArmReport: one arm's provenance, level metrics, and aggregate quality.
- TokenCostComparison: observed token volume repriced against the configured hosted model.
- VllmBenchReport: complete hash-bound benchmark evidence.
- FrontendVllmBenchData: aggregate browser-safe projection for the later frontend phase.

Key functions:
- mechanical_headline: derive the only allowed benchmark headline from measured data.

Notes:
- Report models contain aggregate synthetic evidence; case prompts and raw outputs stay local.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from lib.vllm_bench.config import ArmName
from lib.vllm_bench.metrics import LevelMetrics
from lib.vllm_bench.quality import QualitySummary
from lib.vllm_bench.state import ServerProvenance

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    extra="forbid",
    alias_generator=to_camel,
    populate_by_name=True,
    protected_namespaces=(),
)
_HASH = r"^[0-9a-f]{64}$"


class AcceptanceCheck(BaseModel):
    """One named data-derived publication criterion."""

    model_config = _MODEL_CONFIG

    name: str = Field(..., min_length=1, description="Stable criterion name.")
    passed: bool = Field(..., description="Whether the observed evidence meets the criterion.")
    observed: str = Field(..., min_length=1, description="Human-readable observed value.")
    required: str = Field(..., min_length=1, description="Frozen required value.")


class QualityDelta(BaseModel):
    """AWQ-minus-BF16 percentage-point quality delta and warning outcome."""

    model_config = _MODEL_CONFIG

    metric: str = Field(..., min_length=1, description="Quality metric name.")
    delta_percentage_points: float = Field(..., description="AWQ minus BF16 points.")
    warning: bool = Field(..., description="Whether absolute delta exceeds the warning limit.")


class ArmReport(BaseModel):
    """One benchmark arm's immutable server provenance and derived measurements."""

    model_config = _MODEL_CONFIG

    arm: ArmName = Field(..., description="BF16 or AWQ arm.")
    server: ServerProvenance = Field(..., description="Model/image/GPU/price provenance.")
    levels: tuple[LevelMetrics, ...] = Field(..., min_length=1, description="Concurrency levels.")
    quality: QualitySummary = Field(..., description="Arm quality including abstention fixtures.")
    total_cost_usd: float = Field(..., ge=0, description="Sum of measured level host costs.")


class TokenCostComparison(BaseModel):
    """Observed one-level tokens repriced against the configured hosted comparison model."""

    model_config = _MODEL_CONFIG

    model: str = Field(..., min_length=1, description="Comparison model reference.")
    prompt_tokens: int = Field(..., ge=0, description="Observed prompt tokens.")
    completion_tokens: int = Field(..., ge=0, description="Observed output tokens.")
    estimated_cost_usd: float = Field(..., ge=0, description="Configured hosted token cost.")


class VllmBenchReport(BaseModel):
    """Complete hash-bound BF16-versus-AWQ benchmark evidence report."""

    model_config = _MODEL_CONFIG

    report_version: str = Field(..., min_length=1, description="Report schema version.")
    run_id: str = Field(..., min_length=1, description="Benchmark run identity.")
    protocol_version: str = Field(..., min_length=1, description="Protocol version.")
    profile: str = Field(..., min_length=1, description="Execution profile.")
    case_source: str = Field(..., min_length=1, description="Case builder source.")
    config_sha256: str = Field(..., pattern=_HASH, description="Exact config hash.")
    cases_sha256: str = Field(..., pattern=_HASH, description="Exact case artifact hash.")
    prompt_version: str = Field(..., min_length=1, description="Production prompt version.")
    prompt_sha256: str = Field(..., pattern=_HASH, description="Production prompt hash.")
    measured_cases: int = Field(..., gt=0, description="Performance-measured synthetic cases.")
    abstention_cases: int = Field(..., ge=0, description="Separate quality fixtures.")
    started_at: datetime = Field(..., description="Run start timestamp.")
    completed_at: datetime = Field(..., description="Full matrix completion timestamp.")
    kv_cache_mode: str = Field(..., min_length=1, description="KV-cache comparison mode.")
    arms: tuple[ArmReport, ArmReport] = Field(..., description="BF16 then AWQ reports.")
    weight_memory_reduction: float = Field(..., description="Parsed AWQ reduction fraction.")
    safetensors_reduction: float = Field(..., description="Secondary weight-file reduction.")
    quality_deltas: tuple[QualityDelta, ...] = Field(..., description="AWQ minus BF16 deltas.")
    token_cost_comparison: TokenCostComparison | None = Field(
        default=None, description="Optional hosted token-cost comparison."
    )
    acceptance: tuple[AcceptanceCheck, ...] = Field(..., min_length=1, description="Criteria.")
    acceptance_met: bool = Field(..., description="Whether every criterion passed.")
    headline: str = Field(..., min_length=1, description="Mechanically derived headline.")

    @model_validator(mode="after")
    def _derived_invariants(self) -> VllmBenchReport:
        if tuple(item.arm for item in self.arms) != ("bf16", "awq"):
            raise ValueError("report arms must be ordered bf16 then awq")
        if self.acceptance_met != all(item.passed for item in self.acceptance):
            raise ValueError("acceptanceMet must equal all acceptance checks")
        expected = mechanical_headline(
            weight_reduction=self.weight_memory_reduction,
            bf16=self.arms[0],
            awq=self.arms[1],
            failed=tuple(item.name for item in self.acceptance if not item.passed),
        )
        if self.headline != expected:
            raise ValueError("headline must be mechanically derived from report data")
        return self


class FrontendVllmBenchData(BaseModel):
    """Hash-bound aggregate browser projection consumed by Phase 12."""

    model_config = _MODEL_CONFIG

    report_sha256: str = Field(..., pattern=_HASH, description="Full report byte hash.")
    run_id: str = Field(..., min_length=1, description="Source report run identity.")
    headline: str = Field(..., min_length=1, description="Mechanical report headline.")
    acceptance_met: bool = Field(..., description="Acceptance outcome.")
    measured_cases: int = Field(..., gt=0, description="Measured case count.")
    weight_memory_reduction: float = Field(..., description="AWQ weight-memory reduction.")
    arms: tuple[ArmReport, ArmReport] = Field(..., description="Aggregate arm evidence.")
    quality_deltas: tuple[QualityDelta, ...] = Field(..., description="Quality comparisons.")


def mechanical_headline(
    *,
    weight_reduction: float,
    bf16: ArmReport,
    awq: ArmReport,
    failed: tuple[str, ...],
) -> str:
    """Derive memory and highest-concurrency throughput wording without authored claims."""
    bf16_level = max(bf16.levels, key=lambda item: item.concurrency)
    awq_level = max(awq.levels, key=lambda item: item.concurrency)
    baseline = bf16_level.requests_per_second
    delta = ((awq_level.requests_per_second / baseline) - 1) * 100 if baseline else 0.0
    if delta < 0:
        speed = f"AWQ slower by {abs(delta):.1f}% at concurrency {awq_level.concurrency}"
    else:
        speed = f"AWQ throughput higher by {delta:.1f}% at concurrency {awq_level.concurrency}"
    core = f"AWQ reduced parsed model-weight memory by {weight_reduction:.1%}; {speed}."
    if failed:
        return f"Acceptance NOT met ({', '.join(failed)}). {core}"
    return core
