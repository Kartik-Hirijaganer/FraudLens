"""Summary: Strict publishable contracts for the scenario-shaped gated-cascade benchmark report
(release 0.5.0 Phase 5, carried risk 11). The v1 report is a TWO-ARM contract — `arms` is literally
`tuple[ArmReport, ArmReport]` ordered bf16-then-awq — so it cannot express a run whose unit of
measurement is a production SAR profile measured over one or two endpoint roles. This module adds
the scenario-shaped contract alongside it; `report_models.VllmBenchReport` and the already-published
v1 artifact are untouched and stay valid forever (AD-1.3).

Every comparison carries `same_resource`, because a two-endpoint cascade p95 next to a
single-endpoint baseline p95 is an ARCHITECTURE comparison, not a same-hardware one, and a reader
who cannot see which is which will read a real cost as a free win (AD-4.3, AD-4.4, carried risk 8).

Key classes:
- ExternalRouteSnapshot: a hosted tier's route and zero-data-retention eligibility at readiness.
- EndpointProvenance: one named endpoint role bound to its observed server provenance.
- CascadeProvenance: run-level identity, endpoint provenance, and external-route eligibility.
- ScenarioReport: one production SAR profile's measured levels, stages, quality, and cost.
- CascadeComparison: one scenario-versus-baseline figure, labelled by what it compares.
- CascadeBenchReport: complete hash-bound gated-cascade evidence.

Key functions:
- cascade_mechanical_headline: derive the only allowed cascade headline from measured data.

Notes:
- The headline is derived, never authored: it restates the served share, the escalated share, the
  p95 change against the baseline scenario, and the weight-memory reduction, all read off the
  typed report. A model validator re-derives it, so a hand-edited headline fails to parse.
- `cuda_version` and `external_routes` are OPTIONAL and published as absent when absent. The
  0.5.0 full run predates run-time CUDA capture and exercised no hosted tier; inferring a CUDA
  version from the driver version, or asserting a retention posture never queried, would be
  fabricated provenance (carried risk 10).
- Disclosures are configured data, not prose compiled into the builder, so what the report admits
  about itself is reviewable in `config/vllm-bench.yaml` next to the protocol it describes.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from lib.vllm_bench.metrics import LevelMetrics
from lib.vllm_bench.quality import QualitySummary
from lib.vllm_bench.report_models import AcceptanceCheck
from lib.vllm_bench.state import ServerProvenance

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    extra="forbid",
    alias_generator=to_camel,
    populate_by_name=True,
    protected_namespaces=(),
)
_HASH = r"^[0-9a-f]{64}$"
CASCADE_REPORT_VERSION = "vllm-cascade-report-v1"


class ExternalRouteSnapshot(BaseModel):
    """One hosted tier's served route and retention eligibility, captured at readiness."""

    model_config = _MODEL_CONFIG

    connection: str = Field(..., min_length=1, description="Named production connection.")
    model: str = Field(..., min_length=1, description="Model reference the route resolved to.")
    upstream_provider: str = Field(..., min_length=1, description="Upstream the route selected.")
    zero_data_retention: bool = Field(..., description="Whether the route asserted ZDR.")
    captured_at: datetime = Field(..., description="When readiness observed this eligibility.")


class EndpointProvenance(BaseModel):
    """One named endpoint role bound to the server provenance actually observed for it."""

    model_config = _MODEL_CONFIG

    role: str = Field(..., min_length=1, description="Endpoint role name in the scenario matrix.")
    server: ServerProvenance = Field(..., description="Model/image/GPU/CUDA/price provenance.")


class CascadeProvenance(BaseModel):
    """Run-level identity plus every endpoint and hosted route the matrix actually used."""

    model_config = _MODEL_CONFIG

    git_commit: str = Field(..., min_length=7, description="Commit the matrix executed from.")
    quality_policy_sha256: str = Field(
        ..., pattern=_HASH, description="The single gate policy every attempt was judged under."
    )
    endpoints: tuple[EndpointProvenance, ...] = Field(
        ..., min_length=1, description="Endpoint roles in stable name order."
    )
    external_routes: tuple[ExternalRouteSnapshot, ...] = Field(
        default=(), description="Hosted-tier eligibility snapshots; empty when none was exercised."
    )

    @model_validator(mode="after")
    def _named_once(self) -> CascadeProvenance:
        roles = [item.role for item in self.endpoints]
        if roles != sorted(roles) or len(set(roles)) != len(roles):
            raise ValueError("endpoint provenance must be unique and in stable role order")
        return self


class ScenarioReport(BaseModel):
    """One measured production SAR profile: its stages, levels, quality, and host cost."""

    model_config = _MODEL_CONFIG

    name: str = Field(..., min_length=1, description="Scenario identity in the frozen matrix.")
    profile: str = Field(..., min_length=1, description="Production SAR routing profile measured.")
    endpoint_roles: tuple[str, ...] = Field(
        ..., min_length=1, description="Endpoint roles this scenario kept provisioned."
    )
    stages: tuple[str, ...] = Field(
        ..., min_length=1, description="Cascade stages the profile declares, in escalation order."
    )
    levels: tuple[LevelMetrics, ...] = Field(
        ..., min_length=1, description="Measured concurrency levels in ascending order."
    )
    quality: QualitySummary = Field(..., description="Deterministic quality at the primary level.")
    total_cost_usd: float = Field(..., ge=0, description="Summed measured level host cost.")

    @model_validator(mode="after")
    def _ascending_levels(self) -> ScenarioReport:
        levels = [item.concurrency for item in self.levels]
        if levels != sorted(levels) or len(set(levels)) != len(levels):
            raise ValueError("scenario levels must be unique and ascending")
        return self


class CascadeComparison(BaseModel):
    """One scenario-versus-baseline figure, labelled by exactly what it compares."""

    model_config = _MODEL_CONFIG

    metric: str = Field(..., min_length=1, description="Compared measurement name.")
    concurrency: int = Field(..., gt=0, description="Shared concurrency level.")
    baseline_scenario: str = Field(..., min_length=1, description="Scenario used as the baseline.")
    baseline: float = Field(..., description="Baseline observed value.")
    scenario: str = Field(..., min_length=1, description="Scenario compared against it.")
    observed: float = Field(..., description="Scenario observed value.")
    change_pct: float = Field(..., description="Signed percentage change from baseline.")
    same_resource: bool = Field(
        ...,
        description="True only when both sides kept the same endpoint count (AD-4.3).",
    )


class CascadeBenchReport(BaseModel):
    """Complete hash-bound gated-cascade benchmark evidence for a scenario-shaped run."""

    model_config = _MODEL_CONFIG

    report_version: str = Field(..., min_length=1, description="Report schema version.")
    run_id: str = Field(..., min_length=1, description="Benchmark run identity.")
    protocol_version: str = Field(..., min_length=1, description="Frozen protocol version.")
    profile: str = Field(..., min_length=1, description="Execution profile.")
    case_source: str = Field(..., min_length=1, description="Case builder source.")
    config_sha256: str = Field(..., pattern=_HASH, description="Exact benchmark config hash.")
    cases_sha256: str = Field(..., pattern=_HASH, description="Exact case artifact hash.")
    prompt_version: str = Field(..., min_length=1, description="Production prompt version.")
    prompt_sha256: str = Field(..., pattern=_HASH, description="Production prompt hash.")
    measured_cases: int = Field(..., gt=0, description="Performance-measured synthetic cases.")
    started_at: datetime = Field(..., description="Matrix start timestamp.")
    completed_at: datetime = Field(..., description="Matrix completion timestamp.")
    provenance: CascadeProvenance = Field(..., description="Endpoint and route provenance.")
    baseline_scenario: str = Field(..., min_length=1, description="Scenario every delta is vs.")
    scenarios: tuple[ScenarioReport, ...] = Field(
        ..., min_length=1, description="Measured scenarios in frozen matrix order."
    )
    weight_memory_reduction: float = Field(..., description="Parsed AWQ weight reduction.")
    comparisons: tuple[CascadeComparison, ...] = Field(
        ..., min_length=1, description="Labelled scenario-versus-baseline figures."
    )
    disclosures: tuple[str, ...] = Field(
        ..., min_length=1, description="Configured limitations this evidence is published with."
    )
    acceptance: tuple[AcceptanceCheck, ...] = Field(..., min_length=1, description="Criteria.")
    acceptance_met: bool = Field(..., description="Whether every criterion passed.")
    headline: str = Field(..., min_length=1, description="Mechanically derived headline.")

    def scenario(self, name: str) -> ScenarioReport:
        """Return one measured scenario or fail closed on an unknown name."""
        for item in self.scenarios:
            if item.name == name:
                return item
        raise ValueError(f"report has no scenario '{name}'")

    @property
    def cascade_scenarios(self) -> tuple[ScenarioReport, ...]:
        """Return the measured scenarios that actually declare more than one stage."""
        return tuple(item for item in self.scenarios if len(item.stages) > 1)

    @model_validator(mode="after")
    def _derived_invariants(self) -> CascadeBenchReport:
        names = [item.name for item in self.scenarios]
        if len(set(names)) != len(names):
            raise ValueError("measured scenario names must be unique")
        if self.baseline_scenario not in names:
            raise ValueError("baselineScenario must name a measured scenario")
        if not self.cascade_scenarios:
            raise ValueError("a cascade report must measure at least one multi-stage scenario")
        roles = {item.role for item in self.provenance.endpoints}
        unknown = {role for item in self.scenarios for role in item.endpoint_roles} - roles
        if unknown:
            raise ValueError(f"scenarios use roles without provenance: {sorted(unknown)}")
        if self.acceptance_met != all(item.passed for item in self.acceptance):
            raise ValueError("acceptanceMet must equal all acceptance checks")
        if self.headline != cascade_mechanical_headline(
            scenarios=self.scenarios,
            comparisons=self.comparisons,
            weight_memory_reduction=self.weight_memory_reduction,
            acceptance=self.acceptance,
        ):
            raise ValueError("headline must be mechanically derived from report data")
        return self


CASCADE_LATENCY_METRIC = "cascadeLatencyP95Ms"


def _primary_cascade(
    scenarios: Sequence[ScenarioReport],
) -> tuple[ScenarioReport, LevelMetrics]:
    """Return the cascade scenario and level the headline speaks for: the highest concurrency."""
    multi_stage = [item for item in scenarios if len(item.stages) > 1]
    if not multi_stage:
        raise ValueError("a cascade headline requires at least one multi-stage scenario")
    scenario = max(multi_stage, key=lambda item: max(level.concurrency for level in item.levels))
    return scenario, max(scenario.levels, key=lambda item: item.concurrency)


def _latency_clause(
    comparisons: Sequence[CascadeComparison], scenario: ScenarioReport, concurrency: int
) -> str:
    """Describe the case-latency change against the baseline, and what the two sides were."""
    found = next(
        (
            item
            for item in comparisons
            if item.metric == CASCADE_LATENCY_METRIC
            and item.scenario == scenario.name
            and item.concurrency == concurrency
        ),
        None,
    )
    if found is None:
        return ""
    direction = "higher" if found.change_pct > 0 else "lower"
    resource = "" if found.same_resource else " across two endpoints, not one"
    return (
        f"; case p95 latency {abs(found.change_pct):.1f}% {direction} than "
        f"{found.baseline_scenario} at concurrency {concurrency}{resource}"
    )


def cascade_mechanical_headline(
    *,
    scenarios: Sequence[ScenarioReport],
    comparisons: Sequence[CascadeComparison],
    weight_memory_reduction: float,
    acceptance: Sequence[AcceptanceCheck],
) -> str:
    """Derive served share, escalated share, p95 change, and weight reduction from measured data."""
    scenario, level = _primary_cascade(scenarios)
    if level.cascade is None:
        raise ValueError("the headline cascade level carries no composed cascade metrics")
    core = (
        f"Gated {scenario.profile} served {level.cascade.final_pass_rate:.1%} of "
        f"{level.cascade.cases} cases with {level.cascade.escalation_rate:.1%} escalated"
        f"{_latency_clause(comparisons, scenario, level.concurrency)}"
        f"; AWQ model weights {weight_memory_reduction:.1%} smaller."
    )
    failed = tuple(item.name for item in acceptance if not item.passed)
    if failed:
        return f"Acceptance NOT met ({', '.join(failed)}). {core}"
    return core
