"""Summary: Closed-loop asyncio load execution with warm-up exclusion and resumable levels.

Key classes:
- (none)

Key functions:
- ordered_cases: derive the identical deterministic request order used by both arms.
- run_level: execute one complete concurrency level and collect its telemetry window.
- run_arm: resume or execute every configured level for one arm.

Notes:
- A level is persisted only after all measured requests terminate; interrupted partial work reruns.
"""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime
from pathlib import Path

from lib.study import sha256_hex
from lib.vllm_bench.client import OpenAiCompatibleStreamClient
from lib.vllm_bench.config import ArmName, VllmBenchConfig, resolve_profile
from lib.vllm_bench.state import (
    BenchmarkCase,
    CaseArtifact,
    LevelCheckpoint,
    RequestMeasurement,
    RunManifest,
    ServerProvenance,
    initialize_run,
    validate_restart_memory,
    write_run,
)
from lib.vllm_bench.telemetry import GpuSampler, TelemetrySample


def ordered_cases(
    artifact: CaseArtifact,
    config: VllmBenchConfig,
    *,
    profile: str,
    concurrency: int,
) -> tuple[BenchmarkCase, ...]:
    """Return the seeded measured-case order shared by both arms for one level."""
    requested, _levels, _warmups = resolve_profile(config, profile)
    measured = [case for case in artifact.cases if case.case_set == "measured"]
    if len(measured) < requested:
        raise ValueError(f"case artifact has {len(measured)} measured cases; {requested} required")
    selected = measured[:requested]
    random.Random(config.seed + concurrency).shuffle(selected)
    return tuple(selected)


async def _collect_telemetry(
    sampler: GpuSampler, stop_event: asyncio.Event, samples: list[TelemetrySample], interval: float
) -> None:
    """Sample until the measured request window closes, tolerating transient sampler failures."""
    while not stop_event.is_set():
        try:
            sample = await sampler.sample()
        except (OSError, RuntimeError, ValueError):
            sample = None
        if sample is not None:
            samples.append(sample)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            continue


async def run_level(  # noqa: PLR0913 - explicit run inputs enforce fairness and test seams.
    *,
    arm: ArmName,
    concurrency: int,
    artifact: CaseArtifact,
    cases_sha256: str,
    config: VllmBenchConfig,
    profile: str,
    client: OpenAiCompatibleStreamClient,
    sampler: GpuSampler,
) -> LevelCheckpoint:
    """Run excluded warm-ups, then one complete closed-loop measured level."""
    ordered = ordered_cases(artifact, config, profile=profile, concurrency=concurrency)
    _requested, _levels, warmup_count = resolve_profile(config, profile)
    warmups = [case for case in artifact.cases if case.case_set == "warmup"]
    if len(warmups) < warmup_count:
        raise ValueError("case artifact does not contain enough warm-up cases")
    for sequence, case in enumerate(warmups[:warmup_count]):
        measurement = await client.generate(
            case,
            sequence=sequence,
            seed=config.seed + sequence,
        )
        if measurement.error_code is not None:
            raise RuntimeError("warm-up request failed; measured level was not started")
    started_at = datetime.now(UTC)
    semaphore = asyncio.Semaphore(concurrency)
    telemetry: list[TelemetrySample] = []
    stop_event = asyncio.Event()
    collector = asyncio.create_task(
        _collect_telemetry(sampler, stop_event, telemetry, config.telemetry.interval_s)
    )

    async def invoke(sequence: int, case: BenchmarkCase) -> RequestMeasurement:
        async with semaphore:
            return await client.generate(
                case,
                sequence=sequence,
                seed=config.seed + sequence,
            )

    try:
        results = await asyncio.gather(
            *(invoke(sequence, case) for sequence, case in enumerate(ordered))
        )
    finally:
        stop_event.set()
        await collector
    completed_at = datetime.now(UTC)
    quality_measurements: tuple[RequestMeasurement, ...] = ()
    if concurrency == min(resolve_profile(config, profile)[1]):
        abstentions = [case for case in artifact.cases if case.case_set == "abstention"]
        quality_measurements = tuple(
            await asyncio.gather(
                *(
                    client.generate(
                        case,
                        sequence=sequence,
                        seed=config.seed + len(ordered) + sequence,
                    )
                    for sequence, case in enumerate(abstentions)
                )
            )
        )
    case_order_sha256 = sha256_hex("\n".join(case.case_id for case in ordered))
    return LevelCheckpoint(
        arm=arm,
        concurrency=concurrency,
        case_order_sha256=case_order_sha256,
        cases_sha256=cases_sha256,
        started_at=started_at,
        completed_at=completed_at,
        warmup_completed=warmup_count,
        measurements=tuple(results),
        quality_measurements=quality_measurements,
        telemetry=tuple(telemetry),
    )


def _bind_server(manifest: RunManifest, provenance: ServerProvenance) -> RunManifest:
    """Validate restart identity/memory and attach first-seen per-arm provenance."""
    prior = manifest.servers.get(provenance.arm)
    if prior is not None:
        validate_restart_memory(prior.weight_memory_gib, provenance.weight_memory_gib)
        normalized = prior.model_copy(update={"weight_memory_gib": provenance.weight_memory_gib})
        if normalized != provenance:
            raise ValueError("server provenance drifted while resuming an arm")
        return manifest
    return manifest.model_copy(update={"servers": {**manifest.servers, provenance.arm: provenance}})


async def run_arm(  # noqa: PLR0913 - explicit inputs keep external IO injectable and governed.
    *,
    run_path: Path,
    run_id: str,
    arm: ArmName,
    artifact: CaseArtifact,
    cases_sha256: str,
    config: VllmBenchConfig,
    profile: str,
    provenance: ServerProvenance,
    client: OpenAiCompatibleStreamClient,
    sampler: GpuSampler,
) -> RunManifest:
    """Resume completed levels and atomically checkpoint each newly completed level."""
    manifest = initialize_run(
        run_path,
        run_id=run_id,
        config=config,
        profile=profile,
        cases_sha256=cases_sha256,
        started_at=datetime.now(UTC),
    )
    manifest = _bind_server(manifest, provenance)
    write_run(run_path, manifest)
    _count, levels, _warmups = resolve_profile(config, profile)
    for index, concurrency in enumerate(levels):
        key = f"{arm}:{concurrency}"
        existing = manifest.levels.get(key)
        if existing is not None:
            if existing.cases_sha256 != cases_sha256:
                raise ValueError("completed level case hash does not match the current corpus")
            continue
        checkpoint = await run_level(
            arm=arm,
            concurrency=concurrency,
            artifact=artifact,
            cases_sha256=cases_sha256,
            config=config,
            profile=profile,
            client=client,
            sampler=sampler,
        )
        manifest = manifest.model_copy(update={"levels": {**manifest.levels, key: checkpoint}})
        write_run(run_path, manifest)
        if index < len(levels) - 1 and config.load.cooldown_s:
            await asyncio.sleep(config.load.cooldown_s)
    expected = {f"{name}:{level}" for name in config.arms for level in levels}
    if expected.issubset(manifest.levels):
        manifest = manifest.model_copy(update={"completed_at": datetime.now(UTC)})
        write_run(run_path, manifest)
    return manifest
