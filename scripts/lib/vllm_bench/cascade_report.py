"""Summary: Scenario-shaped gated-cascade report assembly and validation (release 0.5.0 Phase 5,
carried risk 11). `report.build_report` validates and derives a TWO-ARM matrix; this builder does
the same job for a run whose unit is a production SAR profile measured over one or two endpoint
roles. It reuses every derivation Phase 4 already landed — `build_level_metrics` (and through it
`cascade_metrics`, per-role telemetry, aggregate resident memory, and GPU-hours per case) — so
nothing here is a second measurement implementation (AD-4.1).

The parity check is the point of the `gate_verdict_parity` criterion: a live scenario records the
production gate's verdict on every attempt, and the report re-derives that verdict from the
persisted output. Publishing requires the two to agree exactly. That is what makes the derived
quality figures evidence about the shipped path rather than a second opinion about it, and it is
why the v1 direct model-only load runner could be retired instead of kept as a cross-check.

Key classes:
- (none)

Key functions:
- build_cascade_report: validate a scenario matrix and derive the complete cascade report.
- write_cascade_report: persist local cascade report JSON and Markdown atomically.
- load_cascade_report: strictly parse one local cascade report.

Notes:
- Only scenarios the run actually executed are reported. A declared-but-unrun scenario is absent
  rather than zero-filled, and the matrix-completeness criterion states which levels were measured.
- Serving errors and gate rejections are counted separately. A case no stage would serve is a
  quality outcome the cascade is DESIGNED to produce; a transport failure is not, and collapsing
  them would let a broken endpoint read as a strict model.
- Comparisons are drawn only at concurrency levels both sides measured, and each one records
  whether the two sides kept the same endpoint count (AD-4.3).
- A run recorded under a SUPERSEDED protocol still reports, bound to the lineage hash that
  records what that protocol's config was. Editing the protocol file may never orphan already
  measured evidence, nor silently re-bless it against bytes it never ran under (AD-1.3).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from fraudlens_backend.sar.factory import SarLlmConfig
from fraudlens_backend.sar.quality_gate import SarQualityGate, load_sar_gate_policy
from lib.study import atomic_write_model, atomic_write_text, sha256_hex
from lib.vllm_bench.cascade import UNSERVED_STAGE, compose_cases
from lib.vllm_bench.cascade_acceptance import (
    cascade_acceptance,
    parity_failures,
    policy_hash,
)
from lib.vllm_bench.cascade_render import render_cascade_markdown
from lib.vllm_bench.cascade_report_models import (
    CASCADE_REPORT_VERSION,
    CascadeBenchReport,
    CascadeComparison,
    CascadeProvenance,
    EndpointProvenance,
    ScenarioReport,
    cascade_mechanical_headline,
)
from lib.vllm_bench.config import ArmName, VllmBenchConfig, resolve_case_set, resolve_profile
from lib.vllm_bench.load import ordered_cases
from lib.vllm_bench.metrics import LevelMetrics, build_level_metrics
from lib.vllm_bench.quality import summarize_quality
from lib.vllm_bench.scenarios import ScenarioConfig
from lib.vllm_bench.server import verify_provenance
from lib.vllm_bench.state import (
    BenchmarkCase,
    CaseArtifact,
    LevelCheckpoint,
    RequestMeasurement,
    RunManifest,
    case_artifact_sha256,
)

_LATENCY_METRIC = "cascadeLatencyP95Ms"
_GPU_METRIC = "gpuHoursPerCase"
_PASS_METRIC = "finalPassRate"


def _executed(
    manifest: RunManifest, config: VllmBenchConfig, levels: tuple[int, ...]
) -> tuple[tuple[ScenarioConfig, tuple[int, ...]], ...]:
    """Return each configured scenario paired with the declared levels this run measured."""
    executed = []
    for scenario in config.cascade.scenarios:
        measured = tuple(
            concurrency
            for concurrency in scenario.concurrency_levels
            if concurrency in levels and f"{scenario.name}:{concurrency}" in manifest.levels
        )
        if measured:
            executed.append((scenario, measured))
    if not executed:
        raise ValueError("the run measured no configured cascade scenario")
    return tuple(executed)


def _require_multi_stage(
    executed: Sequence[tuple[ScenarioConfig, tuple[int, ...]]], sar_config: SarLlmConfig
) -> None:
    """Fail closed on a single-model matrix: it has no escalation to be cascade evidence about."""
    if not any(len(_stages(sar_config, scenario.profile)) > 1 for scenario, _measured in executed):
        raise ValueError("a cascade report requires at least one measured multi-stage scenario")


def _first_attempts(checkpoint: LevelCheckpoint) -> tuple[RequestMeasurement, ...]:
    """Return one measurement per case: the first attempt, in the order the level requested them."""
    return tuple(item for item in checkpoint.measurements if item.attempt_ordinal == 0)


def _validate_checkpoints(
    manifest: RunManifest,
    artifact: CaseArtifact,
    config: VllmBenchConfig,
    executed: Sequence[tuple[ScenarioConfig, tuple[int, ...]]],
    warmups: int,
) -> None:
    """Verify every measured level requested the identical case order the protocol fixes."""
    for scenario, measured in executed:
        for concurrency in measured:
            checkpoint = manifest.levels[f"{scenario.name}:{concurrency}"]
            expected = tuple(
                case.case_id
                for case in ordered_cases(
                    artifact, config, profile=manifest.profile, concurrency=concurrency
                )
            )
            observed = tuple(item.case_id for item in _first_attempts(checkpoint))
            if (
                checkpoint.scenario != scenario.name
                or checkpoint.concurrency != concurrency
                or checkpoint.cases_sha256 != manifest.cases_sha256
                or checkpoint.warmup_completed != warmups
                or checkpoint.case_order_sha256 != sha256_hex("\n".join(expected))
                or observed != expected
            ):
                raise ValueError(
                    f"scenario checkpoint identity or request order drifted: "
                    f"{scenario.name}:{concurrency}"
                )


def _stages(sar_config: SarLlmConfig, profile: str) -> tuple[str, ...]:
    """Return one profile's declared cascade stages in escalation order, from production config."""
    tiers = sar_config.profiles.get(profile)
    if not tiers:
        raise ValueError(f"production SAR config declares no profile '{profile}'")
    return tuple(tier.name for tier in tiers)


def _accepted(checkpoint: LevelCheckpoint, stages: Sequence[str]) -> tuple[RequestMeasurement, ...]:
    """Return only the attempt that actually served each case a draft."""
    served = {
        (case.case_id, case.escalation_tier)
        for case in compose_cases(checkpoint.measurements)
        if case.passed
    }
    return tuple(
        item
        for item in checkpoint.measurements
        if item.stage != UNSERVED_STAGE and (item.case_id, item.attempt_ordinal) in served
    )


def _level_metrics(  # noqa: PLR0913 - pricing and evaluation context stay explicit inputs.
    checkpoint: LevelCheckpoint,
    *,
    scenario: ScenarioConfig,
    manifest: RunManifest,
    config: VllmBenchConfig,
    cases: Mapping[str, BenchmarkCase],
    gate: SarQualityGate,
    stages: Sequence[str],
) -> LevelMetrics:
    """Derive one scenario level through the single shared level-metric implementation."""
    server = manifest.servers[cast(ArmName, config.cascade.endpoints[scenario.endpoints[0]].arm)]
    host = config.cost.hosts[server.host_key]
    return build_level_metrics(
        checkpoint,
        cases=cases,
        hourly_rates_usd={option: float(rate) for option, rate in host.prices.items()},
        purchase_option=server.purchase_option,
        drafts_per_unit=config.cost.drafts_per_unit,
        quality_policy=config.quality,
        gate=gate,
        stages=stages,
        endpoints=len(scenario.endpoints),
    )


def _scenario_report(  # noqa: PLR0913 - the evaluating gate stays an explicit injected input.
    scenario: ScenarioConfig,
    measured: tuple[int, ...],
    *,
    manifest: RunManifest,
    config: VllmBenchConfig,
    cases: Mapping[str, BenchmarkCase],
    gate: SarQualityGate,
    stages: tuple[str, ...],
) -> ScenarioReport:
    """Derive one scenario's levels, accepted-draft quality, and measured host cost."""
    levels = tuple(
        _level_metrics(
            manifest.levels[f"{scenario.name}:{concurrency}"],
            scenario=scenario,
            manifest=manifest,
            config=config,
            cases=cases,
            gate=gate,
            stages=stages,
        )
        for concurrency in measured
    )
    primary = manifest.levels[f"{scenario.name}:{min(measured)}"]
    quality, _details = summarize_quality(cases, _accepted(primary, stages), config.quality, gate)
    return ScenarioReport(
        name=scenario.name,
        profile=scenario.profile,
        endpoint_roles=scenario.endpoints,
        stages=stages,
        levels=levels,
        quality=quality,
        total_cost_usd=sum(item.cost_usd for item in levels),
    )


def _comparison(  # noqa: PLR0913 - a labelled comparison names both of its sides explicitly.
    metric: str,
    concurrency: int,
    *,
    baseline: ScenarioReport,
    baseline_value: float,
    scenario: ScenarioReport,
    observed: float,
) -> CascadeComparison:
    """Build one baseline-versus-scenario figure, labelled with what it actually compares."""
    return CascadeComparison(
        metric=metric,
        concurrency=concurrency,
        baseline_scenario=baseline.name,
        baseline=baseline_value,
        scenario=scenario.name,
        observed=observed,
        change_pct=((observed / baseline_value) - 1) * 100 if baseline_value else 0.0,
        same_resource=len(baseline.endpoint_roles) == len(scenario.endpoint_roles),
    )


def _comparisons(
    reports: Sequence[ScenarioReport], baseline: ScenarioReport
) -> tuple[CascadeComparison, ...]:
    """Compare every other scenario to the baseline at each concurrency level both measured.

    Single-stage scenarios are compared too, and that is deliberate: raw AWQ against BF16 is the
    same-hardware quantization result, and it is the only thing that makes the cascade's number
    mean something. Publishing the cascade alone would hide what it is an improvement ON.
    """
    baseline_levels = {item.concurrency: item for item in baseline.levels}
    comparisons: list[CascadeComparison] = []
    for scenario in reports:
        if scenario.name == baseline.name:
            continue
        for level in scenario.levels:
            reference = baseline_levels.get(level.concurrency)
            if reference is None or level.cascade is None or reference.cascade is None:
                continue
            figures = [
                (
                    _LATENCY_METRIC,
                    reference.cascade.latency_p95_ms,
                    level.cascade.latency_p95_ms,
                ),
                (
                    _PASS_METRIC,
                    reference.cascade.final_pass_rate,
                    level.cascade.final_pass_rate,
                ),
            ]
            if reference.gpu_hours_per_case is not None and level.gpu_hours_per_case is not None:
                figures.append(
                    (_GPU_METRIC, reference.gpu_hours_per_case, level.gpu_hours_per_case)
                )
            comparisons.extend(
                _comparison(
                    metric,
                    level.concurrency,
                    baseline=baseline,
                    baseline_value=left,
                    scenario=scenario,
                    observed=right,
                )
                for metric, left, right in figures
            )
    if not comparisons:
        raise ValueError("no scenario shares a concurrency level with the baseline")
    return tuple(comparisons)


def build_cascade_report(  # noqa: PLR0913 - the evaluating gate and corpus identity stay explicit.
    manifest: RunManifest,
    artifact: CaseArtifact,
    config: VllmBenchConfig,
    sar_config: SarLlmConfig,
    *,
    gate: SarQualityGate | None = None,
    cases_sha256: str | None = None,
) -> CascadeBenchReport:
    """Validate a scenario matrix and derive the complete gated-cascade report.

    `cases_sha256` is the corpus's RECORDED file hash. A persisted corpus is identified by its
    exact bytes, not by a re-serialization of the parsed model: a corpus written under a superseded
    protocol may carry fields the current contract has since removed, and re-hashing the parsed
    form would report drift where the evidence is intact. Omitting it falls back to the
    re-serialized hash, which is correct for a corpus built by the current protocol.
    """
    if manifest.completed_at is None:
        raise ValueError("the scenario matrix must be complete before reporting")
    expected_config = (
        config.config_sha256
        if manifest.protocol_version == config.protocol_version
        else config.protocol_lineage.get(manifest.protocol_version)
    )
    if expected_config is None:
        raise ValueError("the run names a protocol with no recorded config hash")
    if artifact.config_sha256 != expected_config or manifest.config_sha256 != expected_config:
        raise ValueError("benchmark configuration hash drifted")
    if manifest.cases_sha256 != (cases_sha256 or case_artifact_sha256(artifact)):
        raise ValueError("run case hash does not match the canonical case artifact")
    _requested, levels, warmups = resolve_profile(config, manifest.profile)
    executed = _executed(manifest, config, levels)
    _require_multi_stage(executed, sar_config)
    _validate_checkpoints(manifest, artifact, config, executed, warmups)
    for role, endpoint in sorted(config.cascade.endpoints.items()):
        arm = cast(ArmName, endpoint.arm)
        server = manifest.servers.get(arm)
        if server is None:
            continue
        verify_provenance(config, server, arm)
        if server.arm != arm:
            raise ValueError(f"endpoint role '{role}' is bound to the wrong arm")
    evaluator = gate or SarQualityGate(load_sar_gate_policy())
    cases = {case.case_id: case for case in artifact.cases}
    selected_case_set = resolve_case_set(config, manifest.profile)
    reports = tuple(
        _scenario_report(
            scenario,
            measured,
            manifest=manifest,
            config=config,
            cases=cases,
            gate=evaluator,
            stages=_stages(sar_config, scenario.profile),
        )
        for scenario, measured in executed
    )
    roles = sorted({role for item in reports for role in item.endpoint_roles})
    git_commit = manifest.git_commit
    if git_commit is None:
        raise ValueError("a publishable scenario matrix must record the commit it ran from")
    provenance = CascadeProvenance(
        git_commit=git_commit,
        quality_policy_sha256=policy_hash(manifest),
        endpoints=tuple(
            EndpointProvenance(
                role=role,
                server=manifest.servers[cast(ArmName, config.cascade.endpoints[role].arm)],
            )
            for role in roles
        ),
    )
    arms: dict[ArmName, float] = {
        item.server.arm: item.server.weight_memory_gib for item in provenance.endpoints
    }
    weight_reduction = 1 - arms["awq"] / arms["bf16"] if {"awq", "bf16"} <= set(arms) else 0.0
    baseline = next(item for item in reports if item.name == config.cascade.report.baseline)
    comparisons = _comparisons(reports, baseline)
    acceptance = cascade_acceptance(
        config,
        reports=reports,
        executed=executed,
        weight_reduction=weight_reduction,
        parity_mismatches=parity_failures(manifest, cases, evaluator, executed),
        provenance=provenance,
    )
    return CascadeBenchReport(
        report_version=CASCADE_REPORT_VERSION,
        run_id=manifest.run_id,
        protocol_version=manifest.protocol_version,
        profile=manifest.profile,
        case_source=artifact.case_source,
        config_sha256=manifest.config_sha256,
        cases_sha256=manifest.cases_sha256,
        prompt_version=artifact.prompt_version,
        prompt_sha256=artifact.prompt_sha256,
        measured_cases=sum(case.case_set == selected_case_set for case in artifact.cases),
        started_at=manifest.started_at,
        completed_at=manifest.completed_at,
        provenance=provenance,
        baseline_scenario=baseline.name,
        scenarios=reports,
        weight_memory_reduction=weight_reduction,
        comparisons=comparisons,
        disclosures=config.cascade.report.disclosures,
        acceptance=acceptance,
        acceptance_met=all(item.passed for item in acceptance),
        headline=cascade_mechanical_headline(
            scenarios=reports,
            comparisons=comparisons,
            weight_memory_reduction=weight_reduction,
            acceptance=acceptance,
        ),
    )


def write_cascade_report(directory: Path, report: CascadeBenchReport) -> None:
    """Atomically persist local typed JSON and mechanically rendered Markdown."""
    atomic_write_model(directory / "cascade-report.json", report)
    atomic_write_text(directory / "cascade-report.md", render_cascade_markdown(report))


def load_cascade_report(path: Path) -> CascadeBenchReport:
    """Strictly parse one local cascade report."""
    return CascadeBenchReport.model_validate_json(path.read_bytes())


__all__ = ["build_cascade_report", "load_cascade_report", "write_cascade_report"]
