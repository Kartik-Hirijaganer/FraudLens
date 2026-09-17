"""Summary: Cascade composition — the ONE implementation that turns ordered per-attempt
measurements into per-case outcomes and the stage, escalation, latency, and GPU-time rates the
gated-architecture benchmark reports (release 0.5.0 Phase 4.1/4.9). Both producers feed it: the
free replay pilot composes attempts from the persisted 1,000-case run, and a live scenario run
composes the attempts the production `QualityGatedSarDrafter` actually made. Composing both through
one function is what makes the pilot a projection OF the live measurement rather than a second,
differently-derived number.
Case latency here is the plan's definition and nothing looser: request start to the terminal
accepted or failed cascade result, INCLUDING every rejected tier the case paid for. An escalated
case is therefore slower than an unescalated one by construction — that is the finding, not a
defect, and it is why p95 is reported next to the stage mix rather than on its own.

Key classes:
- CascadeCase: one case's composed cascade outcome across every attempt it made.
- CascadeStageTotals: what each configured stage cost, derived from the attempts it made.
- CascadeMetrics: stage, escalation, latency, and GPU-time rates for one composed population.

Key functions:
- compose_cases: group ordered per-attempt measurements into per-case cascade outcomes.
- cascade_metrics: derive stage mix, escalation, pass, latency, and GPU-time rates.

Notes:
- Stage pass rate is measured over the cases that REACHED a stage, never over the whole
  population: a tier-2 pass rate diluted by cases that never escalated would understate it. Rates
  are keyed by the CONFIGURED stage list rather than the observed one, so a stage nothing reached
  reports a real zero instead of vanishing from the table.
- `gpu_seconds_per_case` is aggregate model-occupancy seconds per case (every attempt's served
  latency summed, escalations included). At fixed concurrency it is proportional to GPU-hours, and
  it is reported so cascade throughput can never be read as a free same-resource gain (AD-4.3).
- A case whose final attempt was never gate-evaluated (a transport failure at the last tier) counts
  as a terminal failure, not as an unknown: the analyst got no draft either way.
- Per-stage totals (latency, tokens, retries, serving-error codes, provider cost) come from the
  ATTEMPTS
  rather than the composed cases, because that is the only place a rejected tier's cost survives —
  and what escalation costs is the question the whole release exists to answer.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from lib.vllm_bench.state import RequestMeasurement
from lib.vllm_bench.telemetry import percentile

_MODEL_CONFIG = ConfigDict(
    frozen=True, extra="forbid", alias_generator=to_camel, populate_by_name=True
)
_RATE_TOLERANCE = 1e-9


class CascadeCase(BaseModel):
    """One case's composed cascade outcome: every stage it paid for and what it ended with."""

    model_config = _MODEL_CONFIG

    case_id: str = Field(..., min_length=1, description="Stable synthetic case identity.")
    stages: tuple[str, ...] = Field(
        ..., min_length=1, description="Stage names attempted, in order."
    )
    served_stage: str | None = Field(
        default=None, description="Stage whose draft passed the gate, or None when exhausted."
    )
    escalation_tier: int = Field(
        ..., ge=0, description="Zero-based index of the serving stage (last attempted when failed)."
    )
    passed: bool = Field(..., description="Whether any stage produced a gate-passing draft.")
    reasons: tuple[str, ...] = Field(
        default=(), description="Terminal attempt's PHI-free rejection reason codes."
    )
    error_code: str | None = Field(
        default=None, description="Terminal attempt's transport/policy failure code."
    )
    latency_ms: float = Field(
        ..., ge=0, description="Case latency: every attempt including rejected tiers."
    )
    prompt_tokens: int = Field(..., ge=0, description="Prompt tokens across every attempt.")
    completion_tokens: int = Field(..., ge=0, description="Generated tokens across every attempt.")

    @model_validator(mode="after")
    def _terminal_shape(self) -> CascadeCase:
        if self.passed and self.served_stage is None:
            raise ValueError("a passing case must name the stage that served it")
        if not self.passed and self.served_stage is not None:
            raise ValueError("a failed case cannot name a serving stage")
        if self.escalation_tier >= len(self.stages):
            raise ValueError("escalation tier must index an attempted stage")
        return self


class CascadeStageTotals(BaseModel):
    """What each configured stage actually cost, derived from the attempts it made."""

    model_config = _MODEL_CONFIG

    latency_ms: dict[str, float] = Field(
        ..., min_length=1, description="Total attempt latency each stage accounted for."
    )
    prompt_tokens: dict[str, int] = Field(
        ..., min_length=1, description="Prompt tokens each stage consumed."
    )
    completion_tokens: dict[str, int] = Field(
        ..., min_length=1, description="Generated tokens each stage produced."
    )
    retries: dict[str, int] = Field(
        ..., min_length=1, description="Bounded transport retries each stage performed."
    )
    errors: dict[str, dict[str, int]] = Field(
        ..., min_length=1, description="Serving-error codes each stage returned, counted."
    )
    cost_usd: dict[str, Decimal] = Field(
        ...,
        min_length=1,
        description="Provider-billed cost each stage incurred; zero for a self-hosted stage.",
    )


class CascadeMetrics(BaseModel):
    """Mechanically derived stage, escalation, latency, and GPU-time rates for one population."""

    model_config = _MODEL_CONFIG

    cases: int = Field(..., gt=0, description="Composed cases in this population.")
    stage_pass_rate: dict[str, float] = Field(
        ..., min_length=1, description="Per-stage pass rate over the cases that reached it."
    )
    stage_mix: dict[str, float] = Field(
        ..., min_length=1, description="Share of cases each stage finally served."
    )
    escalation_rate: float = Field(
        ..., ge=0, le=1, description="Share of cases that advanced past the first stage."
    )
    external_rate: float = Field(
        ..., ge=0, le=1, description="Share of cases that reached the final configured stage."
    )
    final_pass_rate: float = Field(
        ..., ge=0, le=1, description="Share of cases some stage served successfully."
    )
    terminal_failure_rate: float = Field(
        ..., ge=0, le=1, description="Share of cases every stage rejected."
    )
    latency_p50_ms: float = Field(..., ge=0, description="Median composed case latency.")
    latency_p95_ms: float = Field(..., ge=0, description="p95 composed case latency.")
    latency_p99_ms: float = Field(..., ge=0, description="p99 composed case latency.")
    gpu_seconds_per_case: float = Field(
        ..., ge=0, description="Aggregate model-occupancy seconds per case, escalations included."
    )
    reason_counts: dict[str, int] = Field(
        default_factory=dict, description="Terminal rejection reason-code distribution."
    )
    stage_totals: CascadeStageTotals = Field(
        ..., description="Per-stage latency, tokens, retries, and serving-error accounting."
    )

    @model_validator(mode="after")
    def _rates_close(self) -> CascadeMetrics:
        if abs(self.final_pass_rate + self.terminal_failure_rate - 1) > _RATE_TOLERANCE:
            raise ValueError("final pass and terminal failure rates must cover every case")
        return self


def _attempt_groups(
    measurements: Sequence[RequestMeasurement],
) -> dict[str, list[RequestMeasurement]]:
    """Group per-attempt measurements by case, preserving attempt order."""
    grouped: dict[str, list[RequestMeasurement]] = {}
    for item in measurements:
        grouped.setdefault(item.case_id, []).append(item)
    return {
        case_id: sorted(attempts, key=lambda item: item.attempt_ordinal)
        for case_id, attempts in grouped.items()
    }


def _composed_case(case_id: str, attempts: Sequence[RequestMeasurement]) -> CascadeCase:
    """Compose one case from its ordered attempts under the plan's case-latency definition."""
    stages = tuple(item.stage or "primary" for item in attempts)
    served = next((index for index, item in enumerate(attempts) if item.gate_passed), None)
    terminal = attempts[served if served is not None else -1]
    usages = [item.usage for item in attempts if item.usage is not None]
    return CascadeCase(
        case_id=case_id,
        stages=stages,
        served_stage=stages[served] if served is not None else None,
        escalation_tier=served if served is not None else len(attempts) - 1,
        passed=served is not None,
        reasons=() if served is not None else terminal.gate_reasons,
        error_code=None if served is not None else terminal.error_code,
        latency_ms=sum(item.latency_s for item in attempts) * 1000,
        prompt_tokens=sum(item.prompt_tokens for item in usages),
        completion_tokens=sum(item.completion_tokens for item in usages),
    )


def compose_cases(measurements: Sequence[RequestMeasurement]) -> tuple[CascadeCase, ...]:
    """Group ordered per-attempt measurements into per-case cascade outcomes."""
    groups = _attempt_groups(measurements)
    for case_id, attempts in groups.items():
        ordinals = [item.attempt_ordinal for item in attempts]
        if ordinals != list(range(len(ordinals))):
            raise ValueError(f"case '{case_id}' has non-contiguous cascade attempts")
    return tuple(_composed_case(case_id, attempts) for case_id, attempts in groups.items())


def _stage_rates(
    cases: Sequence[CascadeCase], stages: Sequence[str]
) -> tuple[dict[str, float], dict[str, float]]:
    """Derive per-stage pass rate over reaching cases and the share each stage finally served."""
    reached = dict.fromkeys(stages, 0)
    served = dict.fromkeys(stages, 0)
    for case in cases:
        for index, stage in enumerate(case.stages):
            reached[stage] += 1
            if case.passed and index == case.escalation_tier:
                served[stage] += 1
    return (
        {stage: served[stage] / reached[stage] if reached[stage] else 0.0 for stage in stages},
        {stage: served[stage] / len(cases) for stage in stages},
    )


def _stage_totals(
    measurements: Sequence[RequestMeasurement], stages: Sequence[str]
) -> CascadeStageTotals:
    """Total latency, tokens, retries, and error codes for each configured stage."""
    latency = dict.fromkeys(stages, 0.0)
    prompt = dict.fromkeys(stages, 0)
    completion = dict.fromkeys(stages, 0)
    retries = dict.fromkeys(stages, 0)
    errors: dict[str, dict[str, int]] = {stage: {} for stage in stages}
    cost = {stage: Decimal("0") for stage in stages}
    for item in measurements:
        stage = item.stage or stages[0]
        latency[stage] += item.latency_s * 1000
        retries[stage] += item.attempts - 1
        cost[stage] += item.cost_usd or Decimal("0")
        if item.usage is not None:
            prompt[stage] += item.usage.prompt_tokens
            completion[stage] += item.usage.completion_tokens
        if item.error_code is not None:
            errors[stage][item.error_code] = errors[stage].get(item.error_code, 0) + 1
    return CascadeStageTotals(
        latency_ms=latency,
        prompt_tokens=prompt,
        completion_tokens=completion,
        retries=retries,
        errors=errors,
        cost_usd=cost,
    )


def _reason_counts(cases: Sequence[CascadeCase]) -> dict[str, int]:
    """Count terminal rejection reason codes over the cases no stage served."""
    counts: dict[str, int] = {}
    for case in cases:
        for reason in case.reasons:
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def cascade_metrics(
    cases: Sequence[CascadeCase],
    *,
    stages: Sequence[str],
    measurements: Sequence[RequestMeasurement] = (),
) -> CascadeMetrics:
    """Derive stage mix, escalation, pass, latency, GPU-time, and per-stage accounting."""
    if not cases:
        raise ValueError("cascade metrics require at least one composed case")
    if not stages or len(set(stages)) != len(stages):
        raise ValueError("configured cascade stages must be unique and non-empty")
    unknown = {stage for case in cases for stage in case.stages} - set(stages)
    if unknown:
        raise ValueError(f"composed cases attempted unconfigured stages: {sorted(unknown)}")
    total = len(cases)
    latencies = [case.latency_ms for case in cases]
    stage_pass_rate, stage_mix = _stage_rates(cases, stages)
    passed = sum(case.passed for case in cases)
    final_stage = stages[-1]
    return CascadeMetrics(
        cases=total,
        stage_pass_rate=stage_pass_rate,
        stage_mix=stage_mix,
        escalation_rate=sum(len(case.stages) > 1 for case in cases) / total,
        external_rate=(
            sum(final_stage in case.stages for case in cases) / total if len(stages) > 1 else 0.0
        ),
        final_pass_rate=passed / total,
        terminal_failure_rate=(total - passed) / total,
        latency_p50_ms=percentile(latencies, 0.50),
        latency_p95_ms=percentile(latencies, 0.95),
        latency_p99_ms=percentile(latencies, 0.99),
        gpu_seconds_per_case=sum(latencies) / 1000 / total,
        reason_counts=_reason_counts(cases),
        stage_totals=_stage_totals(measurements, stages),
    )
