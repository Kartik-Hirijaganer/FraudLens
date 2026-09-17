"""Summary: The FREE replay pilot (release 0.5.0 Phase 4.1) — the admission projection ADR-028
requires before any paid cascade run, computed entirely from the persisted 1,000-case run and
therefore costing nothing. It replays the SHIPPED gate over every recorded per-case output, builds
the attempt sequence the production cascade would have made (tier 1 always; tier 2 only for the
cases tier 1 failed), and composes it through the same `compose_cases`/`cascade_metrics` the live
scenario run uses. That is the point: the pilot is a projection OF the live measurement, not a
second arithmetic, so a live escalation rate far from this one is a real finding about the gate
rather than a difference of method.
It also sizes and costs the declared matrix: each scenario's endpoint-hours are scaled from the
measured per-case latencies of the arms it would run on, inflated by the OVERHEAD RATIO the prior
session actually billed (boot, weight download, smoke, teardown are paid time that no measurement
window contains), priced at the committed rate quote, and put through the same
`project_cost`/`admit` the rest of the experiment governance uses. If the projection does not clear
the allocation, the matrix shrinks — the allocation never grows.

Key classes:
- CascadeLevelProjection: one concurrency level's projected cascade behaviour and cost.
- ScenarioCostProjection: one declared scenario's projected endpoint-hours and USD cost.
- CascadeReplayPilot: the complete committed pilot artifact.

Key functions:
- project_replay_pilot: compose every level and cost the declared matrix from a persisted run.

Notes:
- The external stage has no persisted measurements — it never ran — so the pilot reports the share
  of cases that would REACH it (both self-hosted tiers failed) and never invents its behaviour.
- Latency, GPU-time, and cost projections are scaled from measured per-case latency at the same
  concurrency, so they inherit the run's real queueing rather than assuming linear speed-up.
- Every rate here is recomputed from the persisted evidence on each invocation; nothing in this
  module reads a number from the plan.
- A declared scenario level the persisted run never measured is an ERROR, not a skipped row: an
  admission that silently drops part of the matrix understates the matrix it is admitting.
- Per-arm occupancy is measured for every arm the persisted run covers, not only the replayed
  profile's stages, because the declared matrix also prices the single-endpoint raw scenarios.
- `arm_recall_mean` is the full-run citation recall over the drafts each arm's gate ACCEPTED. The
  committed replay corpus records the same figure as fixture provenance; deriving it here from the
  persisted run lets the offline citation gate cross-check that number against code instead of
  trusting a value typed into a fixture.
- `runId` is the REPLAYED session's id, not a new one: this pilot spends nothing, so it must not
  claim a resource session of its own, and carrying the source id keeps it visible to the ledger's
  published-report coverage check instead of invisible to it.
- The overhead ratio is measured, not assumed: it is the replayed session's BILLED ledger hours
  divided by the hours its own measurement windows account for. Projecting measurement time alone
  would understate a paid run by everything that happens outside a measured level, and an
  admission that understates is worse than no admission.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from fraudlens_backend.sar.factory import SarLlmConfig
from fraudlens_backend.sar.quality_gate import SarQualityGate
from lib.experiments.budget import (
    BudgetConfig,
    LedgerEntry,
    PilotMeasurement,
    admit,
    project_cost,
)
from lib.vllm_bench.cascade import CascadeMetrics, cascade_metrics, compose_cases
from lib.vllm_bench.config import QualityConfig, VllmBenchConfig
from lib.vllm_bench.quality import case_verdict, evaluate_case
from lib.vllm_bench.scenarios import ScenarioConfig
from lib.vllm_bench.state import BenchmarkCase, CaseArtifact, RequestMeasurement, RunManifest

_MODEL_CONFIG = ConfigDict(
    frozen=True, extra="forbid", alias_generator=to_camel, populate_by_name=True
)
PilotVersion = Literal["vllm-cascade-replay-pilot-v1"]
PILOT_VERSION: PilotVersion = "vllm-cascade-replay-pilot-v1"
_SECONDS_PER_HOUR = Decimal("3600")


class CascadeLevelProjection(BaseModel):
    """One concurrency level's projected cascade behaviour against its single-model baseline."""

    model_config = _MODEL_CONFIG

    concurrency: int = Field(..., gt=0, description="Closed-loop concurrency of the source level.")
    cascade: CascadeMetrics = Field(..., description="Composed cascade rates and percentiles.")
    baseline_stage: str = Field(
        ..., min_length=1, description="Stage the cascade is compared against alone."
    )
    baseline_pass_rate: float = Field(
        ..., ge=0, le=1, description="Gate pass rate of the baseline stage run alone."
    )
    baseline_latency_p50_ms: float = Field(..., ge=0, description="Baseline median latency.")
    baseline_latency_p95_ms: float = Field(..., ge=0, description="Baseline p95 latency.")
    baseline_gpu_seconds_per_case: float = Field(
        ..., ge=0, description="Baseline model-occupancy seconds per case."
    )
    latency_p95_delta_pct: float = Field(
        ..., description="Cascade p95 minus baseline p95, as a percentage of the baseline."
    )
    gpu_time_delta_pct: float = Field(
        ..., description="Cascade GPU seconds per case against the baseline, as a percentage."
    )
    arm_gpu_seconds_per_case: dict[str, float] = Field(
        ..., min_length=1, description="Model-occupancy seconds per case for each arm run alone."
    )
    arm_pass_rate: dict[str, float] = Field(
        ..., min_length=1, description="Gate pass rate for each arm run alone."
    )
    arm_recall_mean: dict[str, float] = Field(
        ...,
        min_length=1,
        description="Mean expected-citation recall over the drafts each arm's gate accepted.",
    )


class ScenarioCostProjection(BaseModel):
    """One declared scenario's projected provisioned-endpoint hours and USD cost."""

    model_config = _MODEL_CONFIG

    scenario: str = Field(..., min_length=1, description="Declared scenario name.")
    profile: str = Field(..., min_length=1, description="Production SAR profile measured.")
    endpoints: int = Field(..., gt=0, description="Paid endpoints provisioned simultaneously.")
    concurrency_levels: tuple[int, ...] = Field(
        ..., min_length=1, description="Levels this scenario is measured at."
    )
    cases: int = Field(..., gt=0, description="Measured cases per level.")
    projected_hours: Decimal = Field(..., ge=0, description="Projected endpoint-hours.")
    projected_cost_usd: Decimal = Field(..., ge=0, description="Projected USD at the bound rate.")


class CascadeReplayPilot(BaseModel):
    """The committed zero-spend projection that admits or shrinks the paid cascade matrix."""

    model_config = _MODEL_CONFIG

    pilot_version: PilotVersion = Field(..., description="Pilot artifact schema version.")
    run_id: str = Field(
        ...,
        min_length=1,
        description="Ledgered resource session this pilot's evidence belongs to (no new spend).",
    )
    source_protocol_version: str = Field(..., min_length=1, description="Protocol of that run.")
    source_config_sha256: str = Field(..., min_length=64, description="Config hash of that run.")
    cases_sha256: str = Field(..., min_length=64, description="Case artifact hash of that run.")
    protocol_version: str = Field(..., min_length=1, description="Protocol this pilot admits.")
    profile: str = Field(..., min_length=1, description="SAR profile whose stages were replayed.")
    stages: tuple[str, ...] = Field(..., min_length=1, description="Ordered cascade stage names.")
    replayed_stages: tuple[str, ...] = Field(
        ..., min_length=1, description="Stages the persisted run actually measured."
    )
    policy_version: str = Field(
        ..., min_length=1, description="Gate policy the replay was judged by."
    )
    policy_hash: str = Field(..., min_length=64, description="Exact hash of that gate policy.")
    levels: tuple[CascadeLevelProjection, ...] = Field(
        ..., min_length=1, description="Per-level projections in ascending concurrency."
    )
    matrix: tuple[ScenarioCostProjection, ...] = Field(
        ..., min_length=1, description="Per-scenario cost projection for the declared matrix."
    )
    measured_hours: Decimal = Field(
        ..., ge=0, description="Hours the replayed session's measurement windows account for."
    )
    billed_hours: Decimal = Field(
        ..., ge=0, description="Hours the replayed session was actually billed, from the ledger."
    )
    session_overhead_ratio: Decimal = Field(
        ..., ge=1, description="Billed over measured hours: boot, weights, smoke, and teardown."
    )
    allocation: str = Field(..., min_length=1, description="Budget allocation the matrix draws on.")
    allocation_usd: Decimal = Field(..., gt=0, description="Committed allocation ceiling.")
    projected_hours: Decimal = Field(..., ge=0, description="Total projected endpoint-hours.")
    projected_cost_usd: Decimal = Field(..., ge=0, description="Total projected USD cost.")
    cost_with_margin_usd: Decimal = Field(..., ge=0, description="Projection plus safety margin.")
    admitted: bool = Field(..., description="Whether the declared matrix clears the allocation.")
    reason: str = Field(..., min_length=1, description="Stable admission explanation.")


def _cascade_stages(
    sar_config: SarLlmConfig, config: VllmBenchConfig, profile: str
) -> tuple[tuple[str, ...], dict[str, str]]:
    """Return the profile's ordered stage names and the frozen arm each stage is served by."""
    by_connection = {role.connection: role.arm for role in config.cascade.endpoints.values()}
    stages = sar_config.stages(profile)
    return (
        tuple(stage.name for stage in stages),
        {
            stage.name: by_connection[stage.connection]
            for stage in stages
            if stage.connection in by_connection
        },
    )


class _Replay:
    """The persisted evidence one replay reads: the run, its cases, and the gate judging them."""

    def __init__(
        self, manifest: RunManifest, cases: dict[str, BenchmarkCase], gate: SarQualityGate
    ) -> None:
        """Bind the immutable replay inputs shared by every stage and level."""
        self._manifest = manifest
        self._cases = cases
        self._gate = gate

    def attempt(
        self, ordinal: int, stage: str, measurement: RequestMeasurement
    ) -> RequestMeasurement:
        """Rebuild one persisted observation as the gate-judged attempt it would have been."""
        verdict = case_verdict(self._cases[measurement.case_id], measurement, self._gate)
        return measurement.model_copy(
            update={
                "stage": stage,
                "attempt_ordinal": ordinal,
                "gate_passed": verdict.passed,
                "gate_reasons": (
                    () if verdict.passed else tuple(reason.value for reason in verdict.reasons)
                ),
            }
        )

    def level(self, arm: str, concurrency: int) -> tuple[RequestMeasurement, ...]:
        """Return one arm's measured level from the persisted run."""
        return self._manifest.levels[f"{arm}:{concurrency}"].measurements

    def cascade(self, stage_arms: dict[str, str], concurrency: int) -> CascadeMetrics:
        """Replay every stage in order, escalating only the cases the previous stage failed."""
        attempts: list[RequestMeasurement] = []
        pending: set[str] | None = None
        for ordinal, (stage, arm) in enumerate(stage_arms.items()):
            served = [
                self.attempt(ordinal, stage, measurement)
                for measurement in self.level(arm, concurrency)
                if pending is None or measurement.case_id in pending
            ]
            attempts.extend(served)
            pending = {item.case_id for item in served if not item.gate_passed}
        return cascade_metrics(
            compose_cases(attempts), stages=tuple(stage_arms), measurements=attempts
        )

    def accepted_recall(self, arm: str, concurrency: int, policy: QualityConfig) -> float:
        """Mean ground-truth citation recall over the drafts this arm's gate actually accepted."""
        scores = [
            evaluated.citation_recall
            for measurement in self.level(arm, concurrency)
            if (
                evaluated := evaluate_case(
                    self._cases[measurement.case_id], measurement, policy, self._gate
                )
            ).schema_valid
            and case_verdict(self._cases[measurement.case_id], measurement, self._gate).passed
        ]
        return sum(scores) / len(scores) if scores else 0.0

    def alone(self, stage: str, arm: str, concurrency: int) -> CascadeMetrics:
        """Measure one arm as a single-stage population — the cascade's comparison baseline."""
        attempts = [
            self.attempt(0, stage, measurement) for measurement in self.level(arm, concurrency)
        ]
        return cascade_metrics(compose_cases(attempts), stages=(stage,), measurements=attempts)


def _project_level(
    replay: _Replay,
    concurrency: int,
    stage_arms: dict[str, str],
    arms: tuple[str, ...],
    policy: QualityConfig,
) -> CascadeLevelProjection:
    """Compose one level's cascade projection against its last stage run alone."""
    composed = replay.cascade(stage_arms, concurrency)
    each_arm = {arm: replay.alone(arm, arm, concurrency) for arm in arms}
    baseline_stage = list(stage_arms)[-1]
    alone = each_arm[stage_arms[baseline_stage]]
    return CascadeLevelProjection(
        concurrency=concurrency,
        cascade=composed,
        baseline_stage=baseline_stage,
        baseline_pass_rate=alone.final_pass_rate,
        baseline_latency_p50_ms=alone.latency_p50_ms,
        baseline_latency_p95_ms=alone.latency_p95_ms,
        baseline_gpu_seconds_per_case=alone.gpu_seconds_per_case,
        arm_gpu_seconds_per_case={
            arm: metrics.gpu_seconds_per_case for arm, metrics in each_arm.items()
        },
        arm_pass_rate={arm: metrics.final_pass_rate for arm, metrics in each_arm.items()},
        arm_recall_mean={arm: replay.accepted_recall(arm, concurrency, policy) for arm in arms},
        latency_p95_delta_pct=(
            (composed.latency_p95_ms - alone.latency_p95_ms) / alone.latency_p95_ms * 100
            if alone.latency_p95_ms
            else 0.0
        ),
        gpu_time_delta_pct=(
            (composed.gpu_seconds_per_case - alone.gpu_seconds_per_case)
            / alone.gpu_seconds_per_case
            * 100
            if alone.gpu_seconds_per_case
            else 0.0
        ),
    )


def _level_hours(seconds_per_case: float, cases: int, concurrency: int) -> Decimal:
    """Convert measured per-case occupancy into closed-loop wall-clock hours for one level."""
    return (
        Decimal(str(seconds_per_case)) * Decimal(cases) / Decimal(concurrency) / _SECONDS_PER_HOUR
    )


def _scenario_hours(
    scenario: ScenarioConfig,
    arms: tuple[str, ...],
    levels: dict[int, CascadeLevelProjection],
    cases: int,
) -> Decimal:
    """Project provisioned endpoint-hours from measured per-case occupancy at the same level."""
    endpoints = len(scenario.endpoints)
    missing = [level for level in scenario.concurrency_levels if level not in levels]
    if missing:
        raise ValueError(
            f"scenario '{scenario.name}' declares unmeasured concurrency levels {missing}"
        )
    hours = Decimal("0")
    for concurrency in scenario.concurrency_levels:
        projection = levels[concurrency]
        per_case = (
            projection.cascade.gpu_seconds_per_case
            if endpoints > 1
            else projection.arm_gpu_seconds_per_case[arms[0]]
        )
        hours += _level_hours(per_case, cases, concurrency)
    return hours * Decimal(endpoints)


def _overhead_ratio(
    levels: dict[int, CascadeLevelProjection], cases: int, billed_hours: Decimal
) -> tuple[Decimal, Decimal]:
    """Return the replayed session's measured hours and its billed-over-measured overhead ratio."""
    measured = sum(
        (
            _level_hours(seconds, cases, concurrency)
            for concurrency, projection in levels.items()
            for seconds in projection.arm_gpu_seconds_per_case.values()
        ),
        Decimal("0"),
    )
    if measured <= 0:
        raise ValueError("the replayed session accounts for no measured hours")
    return measured, max(billed_hours / measured, Decimal("1"))


def _billed_hours(ledger: Sequence[LedgerEntry], run_id: str) -> Decimal:
    """Return the billed hours the ledger recorded for the replayed resource session."""
    rows = [entry for entry in ledger if entry.run_id == run_id]
    if len(rows) != 1:
        raise ValueError(f"run '{run_id}' must resolve to exactly one ledger resource session")
    return rows[0].hours


def project_replay_pilot(  # noqa: PLR0913 - every input is explicit committed evidence.
    *,
    manifest: RunManifest,
    artifact: CaseArtifact,
    config: VllmBenchConfig,
    sar_config: SarLlmConfig,
    budget: BudgetConfig,
    ledger: Sequence[LedgerEntry],
    gate: SarQualityGate,
    profile: str,
) -> CascadeReplayPilot:
    """Compose every replayable level and cost the declared matrix without spending anything."""
    cases = {case.case_id: case for case in artifact.cases}
    stages, stage_arms = _cascade_stages(sar_config, config, profile)
    if not stage_arms:
        raise ValueError(f"SAR profile '{profile}' declares no self-hosted stage to replay")
    concurrencies = sorted(
        level
        for level in config.load.concurrency_levels
        if all(f"{arm}:{level}" in manifest.levels for arm in stage_arms.values())
    )
    if not concurrencies:
        raise ValueError("the persisted run measured no level covering every replayable stage")
    replay = _Replay(manifest, cases, gate)
    arms = tuple(
        sorted(
            {
                role.arm
                for role in config.cascade.endpoints.values()
                if all(f"{role.arm}:{level}" in manifest.levels for level in concurrencies)
            }
            | set(stage_arms.values())
        )
    )
    projections = {
        concurrency: _project_level(replay, concurrency, stage_arms, arms, config.quality)
        for concurrency in concurrencies
    }
    measured = sum(case.case_set == "measured" for case in artifact.cases)
    rate = budget.rates[config.cascade.rate_key]
    billed_hours = _billed_hours(ledger, manifest.run_id)
    measured_hours, overhead = _overhead_ratio(projections, measured, billed_hours)
    matrix = tuple(
        ScenarioCostProjection(
            scenario=scenario.name,
            profile=scenario.profile,
            endpoints=len(scenario.endpoints),
            concurrency_levels=scenario.concurrency_levels,
            cases=measured,
            projected_hours=(
                hours := _scenario_hours(
                    scenario,
                    tuple(config.cascade.endpoints[role].arm for role in scenario.endpoints),
                    projections,
                    measured,
                )
                * overhead
            ),
            projected_cost_usd=hours * rate.hourly_rate_usd,
        )
        for scenario in config.cascade.scenarios
    )
    projection = project_cost(
        [
            PilotMeasurement(
                rate_key=config.cascade.rate_key,
                completed_units=Decimal(item.cases),
                target_units=Decimal(item.cases),
                elapsed_hours=item.projected_hours,
                hourly_rate_usd=rate.hourly_rate_usd,
            )
            for item in matrix
        ]
    )
    decision = admit(
        projection,
        budget.allocations[config.cascade.allocation],
        admission_margin=budget.admission_margin,
    )
    return CascadeReplayPilot(
        pilot_version=PILOT_VERSION,
        run_id=manifest.run_id,
        source_protocol_version=manifest.protocol_version,
        source_config_sha256=manifest.config_sha256,
        cases_sha256=manifest.cases_sha256,
        protocol_version=config.protocol_version,
        profile=profile,
        stages=stages,
        replayed_stages=tuple(stage_arms),
        policy_version=gate.policy.policy_version,
        policy_hash=gate.policy_hash,
        levels=tuple(projections[concurrency] for concurrency in concurrencies),
        matrix=matrix,
        measured_hours=measured_hours,
        billed_hours=billed_hours,
        session_overhead_ratio=overhead,
        allocation=config.cascade.allocation,
        allocation_usd=decision.allocation_usd,
        projected_hours=projection.projected_hours,
        projected_cost_usd=decision.projected_cost_usd,
        cost_with_margin_usd=decision.cost_with_margin_usd,
        admitted=decision.admitted,
        reason=decision.reason,
    )
