"""Summary: Scenario execution against the PRODUCTION SAR cascade (release 0.5.0 Phase 4.2/4.5).
A v1 level drove a raw OpenAI-compatible client; a v2 scenario level builds the shipped drafter from
the same `build_sar_drafter` the API uses, hands it prepared `SarInput` records, and records one
measurement per generation ATTEMPT. That is the whole point of the phase: measuring a separate
benchmark client would measure something the product does not do, and measuring only the served
attempt would hide what escalation costs.
Rejected-tier text is never captured — the cascade buffers and discards it so an analyst cannot see
it, and the harness is not privileged — so a rejected attempt carries its recorded gate verdict,
latency, and token usage, and the SERVED attempt carries the structured artifact that was actually
delivered. Quality is therefore measured on what the cascade serves, and escalation behaviour on
what it recorded.

Key classes:
- (none)

Key functions:
- role_telemetry: resolve one endpoint role's GPU sampler settings from env indirection.
- prepared_inputs: return the seeded per-level case order with its production SAR inputs.
- run_scenario_level: execute one complete scenario/concurrency level and collect telemetry.
- run_scenario: resume completed scenario levels and checkpoint each newly completed one.

Notes:
- One drafter instance serves the whole level, exactly as one process serves production traffic,
  so the per-request budget guard and cache behave as they do in the product.
- Telemetry is collected per endpoint ROLE: a two-endpoint cascade samples both GPUs, and a role
  whose sampler fails leaves its samples empty rather than aborting a paid level. Each role's
  sampler command prefix arrives through the env var its config declares, so no Pod address is
  ever committed.
- Each role's observed server provenance is bound into the manifest through the same `bind_server`
  the raw arm runner uses, so a restart that changed weight memory or pins fails closed here too.
- A case that raises is recorded as a terminal failed attempt with a stable error code, never
  dropped: a silently shortened level is exactly the evidence failure this harness exists to avoid.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from fraudlens_ml.sar import (
    SarDrafter,
    SarDraftResult,
    SarDraftStatus,
    SarEventType,
    SarGenerationAttempt,
    SarInput,
)
from lib.study import sha256_hex
from lib.vllm_bench.config import ArmName, TelemetryConfig, VllmBenchConfig, resolve_profile
from lib.vllm_bench.load import bind_server, ordered_cases
from lib.vllm_bench.scenarios import ScenarioConfig
from lib.vllm_bench.state import (
    BenchmarkCase,
    CaseArtifact,
    LevelCheckpoint,
    RequestMeasurement,
    RunManifest,
    ServerProvenance,
    TokenUsage,
    initialize_run,
    write_run,
)
from lib.vllm_bench.telemetry import GpuSampler, TelemetrySample

CASE_ERROR_CODE = "cascade_case_error"
_UNSERVED = "unserved"


def prepared_inputs(
    artifact: CaseArtifact, config: VllmBenchConfig, *, profile: str, concurrency: int
) -> tuple[tuple[BenchmarkCase, SarInput], ...]:
    """Return the seeded case order for one level with the production SAR input of each case."""
    ordered = ordered_cases(artifact, config, profile=profile, concurrency=concurrency)
    missing = [case.case_id for case in ordered if case.sar_input is None]
    if missing:
        raise ValueError(f"{len(missing)} cases carry no SAR input; regenerate the corpus")
    return tuple((case, case.sar_input) for case in ordered if case.sar_input is not None)


def role_telemetry(config: VllmBenchConfig, role: str) -> TelemetryConfig:
    """Resolve one endpoint role's sampler, whose command prefix arrives only through the env."""
    declared = config.cascade.endpoints[role].telemetry_prefix_env
    prefix = os.environ.get(declared, "").strip() if declared else ""
    if not prefix:
        return config.telemetry
    return config.telemetry.model_copy(update={"command_prefix": tuple(prefix.split())})


def _usage(attempt: SarGenerationAttempt) -> TokenUsage | None:
    """Project usage for generated output, including a deterministic gate rejection."""
    if attempt.error_code is not None and attempt.quality is None:
        return None
    return TokenUsage(
        prompt_tokens=attempt.token_usage.input_tokens,
        completion_tokens=attempt.token_usage.output_tokens,
        total_tokens=attempt.token_usage.total_tokens,
    )


def _served_content(result: SarDraftResult, attempt: SarGenerationAttempt) -> str:
    """Return the structured artifact the cascade served, or empty text for a rejected tier."""
    if result.status is not SarDraftStatus.DRAFT or result.structured is None:
        return ""
    if attempt.ordinal != result.escalation_tier:
        return ""
    return result.structured.model_dump_json(by_alias=True)


def _measurements(
    case_id: str, sequence: int, seed: int, started_at: datetime, result: SarDraftResult
) -> tuple[RequestMeasurement, ...]:
    """Record one measurement per generation attempt, rejected tiers included."""
    if not result.attempts:
        return (
            RequestMeasurement(
                case_id=case_id,
                sequence=sequence,
                seed=seed,
                attempts=1,
                started_at=started_at,
                latency_s=0.0,
                error_code=result.error_code or CASE_ERROR_CODE,
                stage=_UNSERVED,
                gate_passed=False,
            ),
        )
    return tuple(
        RequestMeasurement(
            case_id=case_id,
            sequence=sequence,
            seed=seed,
            attempts=attempt.retry_count + 1,
            started_at=started_at,
            latency_s=attempt.latency_ms / 1000,
            content=_served_content(result, attempt),
            finish_reason=None,
            usage=_usage(attempt),
            error_code=None if attempt.quality is not None else attempt.error_code,
            stage=attempt.stage,
            attempt_ordinal=attempt.ordinal,
            connection=attempt.connection,
            served_model=attempt.served_model,
            policy_hash=attempt.quality.policy_hash if attempt.quality is not None else None,
            cost_usd=attempt.cost_usd,
            gate_passed=attempt.quality.passed if attempt.quality is not None else False,
            gate_reasons=(
                tuple(reason.value for reason in attempt.quality.reasons)
                if attempt.quality is not None
                else ()
            ),
        )
        for attempt in result.attempts
    )


async def _draft_case(
    drafter: SarDrafter, sar_input: SarInput, *, case_id: str, sequence: int, seed: int
) -> tuple[RequestMeasurement, ...]:
    """Drive the production cascade for one case and record every attempt it made."""
    started_at = datetime.now(UTC)
    terminal: SarDraftResult | None = None
    async for event in drafter.draft(sar_input):
        if event.type in (SarEventType.COMPLETED, SarEventType.FAILED) and event.result is not None:
            terminal = event.result
    if terminal is None:
        return (
            RequestMeasurement(
                case_id=case_id,
                sequence=sequence,
                seed=seed,
                attempts=1,
                started_at=started_at,
                latency_s=(datetime.now(UTC) - started_at).total_seconds(),
                error_code=CASE_ERROR_CODE,
                stage=_UNSERVED,
                gate_passed=False,
            ),
        )
    return _measurements(case_id, sequence, seed, started_at, terminal)


async def _collect(
    sampler: GpuSampler,
    stop_event: asyncio.Event,
    samples: list[TelemetrySample],
    interval: float,
    role: str,
) -> None:
    """Sample one endpoint role until the measured window closes, tolerating sampler failures."""
    while not stop_event.is_set():
        try:
            sample = await sampler.sample()
        except (OSError, RuntimeError, ValueError):
            sample = None
        if sample is not None:
            samples.append(sample.model_copy(update={"role": role}))
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            continue


async def run_scenario_level(  # noqa: PLR0913 - explicit run inputs keep fairness auditable.
    *,
    scenario: ScenarioConfig,
    concurrency: int,
    artifact: CaseArtifact,
    cases_sha256: str,
    config: VllmBenchConfig,
    profile: str,
    drafter: SarDrafter,
    samplers: Mapping[str, GpuSampler],
) -> LevelCheckpoint:
    """Run excluded warm-ups, then one complete closed-loop scenario level over the cascade."""
    prepared = prepared_inputs(artifact, config, profile=profile, concurrency=concurrency)
    _requested, _levels, warmup_count = resolve_profile(config, profile)
    warmups = [case for case in artifact.cases if case.case_set == "warmup"][:warmup_count]
    if len(warmups) < warmup_count:
        raise ValueError("case artifact does not contain enough warm-up cases")
    for sequence, case in enumerate(warmups):
        if case.sar_input is None:
            raise ValueError("warm-up cases carry no SAR input; regenerate the corpus")
        warmed = await _draft_case(
            drafter,
            case.sar_input,
            case_id=case.case_id,
            sequence=sequence,
            seed=config.seed + sequence,
        )
        if any(item.error_code is not None for item in warmed):
            raise RuntimeError("warm-up request failed; measured level was not started")
    started_at = datetime.now(UTC)
    semaphore = asyncio.Semaphore(concurrency)
    telemetry: dict[str, list[TelemetrySample]] = {role: [] for role in scenario.endpoints}
    stop_event = asyncio.Event()
    collectors = [
        asyncio.create_task(
            _collect(samplers[role], stop_event, telemetry[role], config.telemetry.interval_s, role)
        )
        for role in scenario.endpoints
    ]

    async def invoke(
        sequence: int, case: BenchmarkCase, sar_input: SarInput
    ) -> tuple[RequestMeasurement, ...]:
        async with semaphore:
            return await _draft_case(
                drafter,
                sar_input,
                case_id=case.case_id,
                sequence=sequence,
                seed=config.seed + sequence,
            )

    try:
        results = await asyncio.gather(
            *(
                invoke(sequence, case, sar_input)
                for sequence, (case, sar_input) in enumerate(prepared)
            )
        )
    finally:
        stop_event.set()
        await asyncio.gather(*collectors)
    completed_at = datetime.now(UTC)
    return LevelCheckpoint(
        arm=cast(ArmName, config.cascade.endpoints[scenario.endpoints[0]].arm),
        scenario=scenario.name,
        concurrency=concurrency,
        case_order_sha256=sha256_hex("\n".join(case.case_id for case, _ in prepared)),
        cases_sha256=cases_sha256,
        started_at=started_at,
        completed_at=completed_at,
        warmup_completed=warmup_count,
        measurements=tuple(item for attempts in results for item in attempts),
        telemetry=tuple(sample for role in scenario.endpoints for sample in telemetry[role]),
    )


async def run_scenario(  # noqa: PLR0913 - explicit inputs keep external IO injectable and governed.
    *,
    run_path: Path,
    run_id: str,
    scenario: ScenarioConfig,
    artifact: CaseArtifact,
    cases_sha256: str,
    config: VllmBenchConfig,
    profile: str,
    drafter: SarDrafter,
    samplers: Mapping[str, GpuSampler],
    provenance: Mapping[str, ServerProvenance] | None = None,
    git_commit: str | None = None,
    levels: Sequence[int] = (),
) -> RunManifest:
    """Resume completed scenario levels and atomically checkpoint each newly completed level."""
    manifest = initialize_run(
        run_path,
        run_id=run_id,
        config=config,
        profile=profile,
        cases_sha256=cases_sha256,
        started_at=datetime.now(UTC),
        git_commit=git_commit,
    )
    observed = provenance or {}
    for role in scenario.endpoints:
        if role in observed:
            manifest = bind_server(manifest, observed[role])
    if observed:
        write_run(run_path, manifest)
    selected = tuple(levels) or scenario.concurrency_levels
    for index, concurrency in enumerate(selected):
        key = f"{scenario.name}:{concurrency}"
        existing = manifest.levels.get(key)
        if existing is not None:
            if existing.cases_sha256 != cases_sha256:
                raise ValueError("completed level case hash does not match the current corpus")
            continue
        checkpoint = await run_scenario_level(
            scenario=scenario,
            concurrency=concurrency,
            artifact=artifact,
            cases_sha256=cases_sha256,
            config=config,
            profile=profile,
            drafter=drafter,
            samplers=samplers,
        )
        manifest = manifest.model_copy(update={"levels": {**manifest.levels, key: checkpoint}})
        write_run(run_path, manifest)
        if index < len(selected) - 1 and config.load.cooldown_s:
            await asyncio.sleep(config.load.cooldown_s)
    return manifest
