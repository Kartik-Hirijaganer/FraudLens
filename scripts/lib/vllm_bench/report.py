"""Summary: Benchmark run validation and mechanical report assembly from captured checkpoints.

Key classes:
- (none)

Key functions:
- build_report: validate fairness/completeness and derive performance, quality, and acceptance.
- write_report: persist local report JSON and Markdown atomically.
- load_report: strictly parse one local report.

Notes:
- Reports contain aggregate synthetic evidence only; raw prompts and model outputs remain local.
- The gate that decides quality is INJECTED: a live run is judged by the production runtime policy,
  while replaying already-published prompt-v1 evidence is judged by the policy that output was
  recorded under. Neither is a second evaluator — it is one evaluator with a declared policy.
"""

from __future__ import annotations

import math
from pathlib import Path

from fraudlens_backend.sar.quality_gate import SarQualityGate, load_sar_gate_policy
from lib.study import atomic_write_model, atomic_write_text, sha256_hex
from lib.vllm_bench.config import ArmName, VllmBenchConfig, resolve_profile
from lib.vllm_bench.load import ordered_cases
from lib.vllm_bench.metrics import build_level_metrics
from lib.vllm_bench.quality import QualitySummary, summarize_quality
from lib.vllm_bench.render import render_markdown
from lib.vllm_bench.report_models import (
    REPORT_VERSION,
    AcceptanceCheck,
    ArmReport,
    QualityDelta,
    TokenCostComparison,
    VllmBenchReport,
    mechanical_headline,
)
from lib.vllm_bench.state import (
    BenchmarkCase,
    CaseArtifact,
    RunManifest,
    case_artifact_sha256,
)

_QUALITY_FIELDS = (
    "schema_valid_rate",
    "reference_validity",
    "citation_recall",
    "required_fact_coverage",
    "abstention_correctness",
    "truncation_rate",
)


def _quality_for_arm(  # noqa: PLR0913 - the evaluating gate stays an explicit injected input.
    manifest: RunManifest,
    arm: ArmName,
    levels: tuple[int, ...],
    *,
    cases: dict[str, BenchmarkCase],
    config: VllmBenchConfig,
    gate: SarQualityGate,
) -> QualitySummary:
    """Measure each arm once at the primary level plus its separate abstention fixtures."""
    checkpoint = manifest.levels[f"{arm}:{min(levels)}"]
    summary, _details = summarize_quality(
        cases,
        (*checkpoint.measurements, *checkpoint.quality_measurements),
        config.quality,
        gate,
    )
    return summary


def _arm_report(  # noqa: PLR0913 - the evaluating gate stays an explicit injected input.
    manifest: RunManifest,
    arm: ArmName,
    levels: tuple[int, ...],
    *,
    cases: dict[str, BenchmarkCase],
    config: VllmBenchConfig,
    gate: SarQualityGate,
) -> ArmReport:
    """Derive one arm report in canonical concurrency order."""
    server = manifest.servers[arm]
    host = config.cost.hosts[server.host_key]
    metrics = tuple(
        build_level_metrics(
            manifest.levels[f"{arm}:{concurrency}"],
            cases=cases,
            hourly_rates_usd={option: float(rate) for option, rate in host.prices.items()},
            purchase_option=server.purchase_option,
            drafts_per_unit=config.cost.drafts_per_unit,
            quality_policy=config.quality,
            gate=gate,
        )
        for concurrency in levels
    )
    return ArmReport(
        arm=arm,
        server=server,
        levels=metrics,
        quality=_quality_for_arm(manifest, arm, levels, cases=cases, config=config, gate=gate),
        total_cost_usd=sum(item.cost_usd for item in metrics),
    )


def _validate_provenance(manifest: RunManifest, config: VllmBenchConfig) -> None:
    """Fail closed when either arm or their shared execution environment drifted."""
    left = manifest.servers["bf16"]
    right = manifest.servers["awq"]
    shared = (
        "image_digest",
        "vllm_version",
        "gpu_name",
        "driver_version",
        "host_key",
        "provider",
        "sku",
        "region",
        "purchase_option",
    )
    if any(getattr(left, field) != getattr(right, field) for field in shared):
        raise ValueError("BF16 and AWQ execution provenance is not comparable")
    for arm in config.arms:
        server = manifest.servers[arm]
        selected = config.arms[arm]
        host = config.cost.hosts.get(server.host_key)
        if host is None or server.purchase_option not in host.prices:
            raise ValueError("server cost provenance is absent from the frozen protocol")
        expected = (
            selected.model,
            selected.revision,
            selected.tokenizer,
            selected.tokenizer_revision,
            f"{config.server.image}:{config.server.image_tag}",
            config.server.image_tag,
            float(selected.safetensors_total_gib),
            host.provider,
            host.sku,
            host.region,
            host.price_source_url,
            host.price_verified_at.isoformat(),
        )
        observed = (
            server.model,
            server.model_revision,
            server.tokenizer,
            server.tokenizer_revision,
            server.image,
            server.vllm_version,
            server.safetensors_total_gib,
            server.provider,
            server.sku,
            server.region,
            server.price_source_url,
            server.price_verified_at,
        )
        rate = float(host.prices[server.purchase_option])
        if observed != expected or not math.isclose(server.hourly_rate_usd, rate):
            raise ValueError(f"{arm} server provenance drifted from the frozen protocol")


def _validate_checkpoints(
    manifest: RunManifest,
    artifact: CaseArtifact,
    config: VllmBenchConfig,
    levels: tuple[int, ...],
    warmups: int,
) -> None:
    """Verify checkpoint keys, order, case identity, warm-ups, and abstention execution."""
    abstention_ids = tuple(case.case_id for case in artifact.cases if case.case_set == "abstention")
    primary = min(levels)
    for concurrency in levels:
        expected_cases = ordered_cases(
            artifact,
            config,
            profile=manifest.profile,
            concurrency=concurrency,
        )
        expected_ids = tuple(case.case_id for case in expected_cases)
        order_hash = sha256_hex("\n".join(expected_ids))
        for arm in ("bf16", "awq"):
            checkpoint = manifest.levels[f"{arm}:{concurrency}"]
            observed_ids = tuple(item.case_id for item in checkpoint.measurements)
            quality_ids = tuple(item.case_id for item in checkpoint.quality_measurements)
            expected_quality = abstention_ids if concurrency == primary else ()
            if (
                checkpoint.arm != arm
                or checkpoint.concurrency != concurrency
                or checkpoint.cases_sha256 != manifest.cases_sha256
                or checkpoint.warmup_completed != warmups
                or checkpoint.case_order_sha256 != order_hash
                or observed_ids != expected_ids
                or quality_ids != expected_quality
            ):
                raise ValueError("benchmark checkpoint identity or request order drifted")


def _quality_deltas(
    bf16: QualitySummary, awq: QualitySummary, warning_limit_pp: float
) -> tuple[QualityDelta, ...]:
    """Build AWQ-minus-BF16 percentage-point comparisons for every bounded rate."""
    return tuple(
        QualityDelta(
            metric=field,
            delta_percentage_points=(float(getattr(awq, field)) - float(getattr(bf16, field)))
            * 100,
            warning=(
                abs(float(getattr(awq, field)) - float(getattr(bf16, field))) * 100
                > warning_limit_pp
            ),
        )
        for field in _QUALITY_FIELDS
    )


def _token_comparison(
    manifest: RunManifest, config: VllmBenchConfig, levels: tuple[int, ...]
) -> TokenCostComparison | None:
    """Reprice one complete BF16 case pass against the optional hosted-model rate."""
    comparison = config.cost.comparison_model
    if comparison is None:
        return None
    checkpoint = manifest.levels[f"bf16:{min(levels)}"]
    usages = [item.usage for item in checkpoint.measurements if item.usage is not None]
    prompt = sum(item.prompt_tokens for item in usages)
    completion = sum(item.completion_tokens for item in usages)
    cost = (
        prompt * float(comparison.input_per_million_usd)
        + completion * float(comparison.output_per_million_usd)
    ) / 1_000_000
    return TokenCostComparison(
        model=comparison.model,
        prompt_tokens=prompt,
        completion_tokens=completion,
        estimated_cost_usd=cost,
    )


def _acceptance(  # noqa: PLR0913 - mirrors the explicit publication contract.
    *,
    manifest: RunManifest,
    config: VllmBenchConfig,
    profile: str,
    measured: int,
    requested: int,
    levels: tuple[int, ...],
    bf16: ArmReport,
    awq: ArmReport,
    weight_reduction: float,
) -> tuple[AcceptanceCheck, ...]:
    """Evaluate every Phase-7 publication criterion from captured evidence."""
    all_levels = (*bf16.levels, *awq.levels)
    expected_keys = {f"{arm}:{level}" for arm in config.arms for level in levels}
    complete = expected_keys == set(manifest.levels) and all(
        item.requests == requested for item in all_levels
    )
    minimum_schema_rate = min(bf16.quality.schema_valid_rate, awq.quality.schema_valid_rate)
    minimum_reference_rate = min(bf16.quality.reference_validity, awq.quality.reference_validity)
    return (
        AcceptanceCheck(
            name="profile_full",
            passed=profile == "full",
            observed=profile,
            required="full",
        ),
        AcceptanceCheck(
            name="case_count",
            passed=measured == requested,
            observed=str(measured),
            required=str(requested),
        ),
        AcceptanceCheck(
            name="complete_matrix",
            passed=complete,
            observed=f"{len(manifest.levels)} complete levels",
            required=f"{len(expected_keys)} arms-by-levels with {requested} cases",
        ),
        AcceptanceCheck(
            name="weight_memory_reduction",
            passed=weight_reduction >= config.acceptance.weight_memory_reduction_min,
            observed=f"{weight_reduction:.6f}",
            required=f">={config.acceptance.weight_memory_reduction_min:.6f}",
        ),
        AcceptanceCheck(
            name="error_rate",
            passed=all(item.error_rate <= config.load.max_error_rate for item in all_levels),
            observed=f"max {max(item.error_rate for item in all_levels):.6f}",
            required=f"<={config.load.max_error_rate:.6f}",
        ),
        AcceptanceCheck(
            name="gpu_telemetry",
            passed=(
                not config.acceptance.require_gpu_telemetry
                or all(
                    item.telemetry.samples > 0
                    and item.telemetry.gpu_utilization_mean_pct is not None
                    and item.telemetry.gpu_utilization_p95_pct is not None
                    and item.telemetry.memory_peak_mib is not None
                    for item in all_levels
                )
            ),
            observed=f"min {min(item.telemetry.samples for item in all_levels)} samples",
            required="at least one sample per level",
        ),
        AcceptanceCheck(
            name="token_accounting_drift",
            passed=all(
                item.token_accounting_drift <= config.acceptance.token_accounting_drift_max
                for item in all_levels
            ),
            observed=f"max {max(item.token_accounting_drift for item in all_levels):.6f}",
            required=f"<={config.acceptance.token_accounting_drift_max:.6f}",
        ),
        AcceptanceCheck(
            name="schema_valid_rate",
            passed=all(
                arm.quality.schema_valid_rate >= config.quality.schema_valid_min
                for arm in (bf16, awq)
            ),
            observed=f"min {minimum_schema_rate:.6f}",
            required=f">={config.quality.schema_valid_min:.6f}",
        ),
        AcceptanceCheck(
            name="reference_validity",
            passed=all(
                arm.quality.reference_validity >= config.quality.reference_validity_min
                for arm in (bf16, awq)
            ),
            observed=f"min {minimum_reference_rate:.6f}",
            required=f">={config.quality.reference_validity_min:.6f}",
        ),
    )


def build_report(
    manifest: RunManifest,
    artifact: CaseArtifact,
    config: VllmBenchConfig,
    gate: SarQualityGate | None = None,
) -> VllmBenchReport:
    """Validate fairness/completeness and derive the complete benchmark report."""
    if manifest.completed_at is None or set(manifest.servers) != set(config.arms):
        raise ValueError("both benchmark arms must be complete before reporting")
    if (
        artifact.config_sha256 != config.config_sha256
        or manifest.config_sha256 != config.config_sha256
    ):
        raise ValueError("benchmark configuration hash drifted")
    if manifest.cases_sha256 != case_artifact_sha256(artifact):
        raise ValueError("run case hash does not match the canonical case artifact")
    _validate_provenance(manifest, config)
    cases = {case.case_id: case for case in artifact.cases}
    requested, levels, warmups = resolve_profile(config, manifest.profile)
    measured = sum(case.case_set == "measured" for case in artifact.cases)
    abstention = sum(case.case_set == "abstention" for case in artifact.cases)
    for concurrency in levels:
        left = manifest.levels.get(f"bf16:{concurrency}")
        right = manifest.levels.get(f"awq:{concurrency}")
        if left is None or right is None:
            raise ValueError("benchmark matrix is incomplete")
        if left.case_order_sha256 != right.case_order_sha256:
            raise ValueError("BF16 and AWQ case order differs at a concurrency level")
    _validate_checkpoints(manifest, artifact, config, levels, warmups)
    evaluator = gate or SarQualityGate(load_sar_gate_policy())
    bf16 = _arm_report(manifest, "bf16", levels, cases=cases, config=config, gate=evaluator)
    awq = _arm_report(manifest, "awq", levels, cases=cases, config=config, gate=evaluator)
    weight_reduction = 1 - awq.server.weight_memory_gib / bf16.server.weight_memory_gib
    safetensors_reduction = 1 - awq.server.safetensors_total_gib / bf16.server.safetensors_total_gib
    acceptance = _acceptance(
        manifest=manifest,
        config=config,
        profile=manifest.profile,
        measured=measured,
        requested=requested,
        levels=levels,
        bf16=bf16,
        awq=awq,
        weight_reduction=weight_reduction,
    )
    failed = tuple(item.name for item in acceptance if not item.passed)
    return VllmBenchReport(
        report_version=REPORT_VERSION,
        run_id=manifest.run_id,
        protocol_version=manifest.protocol_version,
        profile=manifest.profile,
        case_source=artifact.case_source,
        config_sha256=manifest.config_sha256,
        cases_sha256=manifest.cases_sha256,
        prompt_version=artifact.prompt_version,
        prompt_sha256=artifact.prompt_sha256,
        measured_cases=measured,
        abstention_cases=abstention,
        started_at=manifest.started_at,
        completed_at=manifest.completed_at,
        kv_cache_mode=config.kv_cache_mode,
        arms=(bf16, awq),
        weight_memory_reduction=weight_reduction,
        safetensors_reduction=safetensors_reduction,
        quality_deltas=_quality_deltas(bf16.quality, awq.quality, config.quality.awq_delta_warn_pp),
        token_cost_comparison=_token_comparison(manifest, config, levels),
        acceptance=acceptance,
        acceptance_met=not failed,
        headline=mechanical_headline(
            report_version=REPORT_VERSION,
            weight_reduction=weight_reduction,
            bf16=bf16,
            awq=awq,
            failed=failed,
        ),
    )


def write_report(directory: Path, report: VllmBenchReport) -> None:
    """Atomically persist local typed JSON and mechanically rendered Markdown."""
    atomic_write_model(directory / "report.json", report)
    atomic_write_text(directory / "report.md", render_markdown(report))


def load_report(path: Path) -> VllmBenchReport:
    """Strictly parse one local benchmark report."""
    return VllmBenchReport.model_validate_json(path.read_bytes())
