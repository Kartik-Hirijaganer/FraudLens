"""Summary: Closed-loop concurrency, fairness, checkpoint, and resume tests.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- Injected fake clients and samplers keep execution provider-free.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from vllm_bench_fakes import (
    HASH,
    RUN_ID,
    FakeSampler,
    FakeStreamClient,
    benchmark_case,
    complete_benchmark,
    server,
    small_config,
)

from lib.vllm_bench.config import load_config
from lib.vllm_bench.load import ordered_cases, run_arm, run_level
from lib.vllm_bench.state import CaseArtifact, load_run, write_run


def _artifact(config) -> CaseArtifact:
    return CaseArtifact(
        protocol_version=config.protocol_version,
        profile="full",
        case_source="ibm-final-test",
        config_sha256=config.config_sha256,
        upstream_sha256=HASH,
        prompt_version="v1",
        prompt_sha256=HASH,
        distinct_subjects=2,
        subject_overlap=0,
        cases=(
            benchmark_case("case-0"),
            benchmark_case("case-1"),
            benchmark_case("warmup-0", "warmup"),
            benchmark_case("abstention-0", "abstention"),
        ),
    )


def test_order_is_deterministic_and_fails_on_case_deficiency() -> None:
    config = small_config(load_config())
    artifact = _artifact(config)
    assert ordered_cases(artifact, config, profile="full", concurrency=2) == ordered_cases(
        artifact, config, profile="full", concurrency=2
    )
    deficient = artifact.model_copy(update={"cases": artifact.cases[1:]})
    with pytest.raises(ValueError, match="required"):
        ordered_cases(deficient, config, profile="full", concurrency=2)


@pytest.mark.asyncio
async def test_level_honors_concurrency_and_excludes_warmup_and_abstention() -> None:
    config = small_config(load_config())
    client = FakeStreamClient()
    checkpoint = await run_level(
        arm="bf16",
        concurrency=2,
        artifact=_artifact(config),
        cases_sha256=HASH,
        config=config,
        profile="full",
        client=client,  # type: ignore[arg-type]
        sampler=FakeSampler(),
    )
    assert client.maximum_active == 2
    assert checkpoint.warmup_completed == 1
    assert len(checkpoint.measurements) == 2
    assert len(checkpoint.quality_measurements) == 0
    assert checkpoint.telemetry


@pytest.mark.asyncio
async def test_primary_level_runs_abstention_and_warmup_failure_stops() -> None:
    config = small_config(load_config())
    client = FakeStreamClient()
    checkpoint = await run_level(
        arm="bf16",
        concurrency=1,
        artifact=_artifact(config),
        cases_sha256=HASH,
        config=config,
        profile="full",
        client=client,  # type: ignore[arg-type]
        sampler=FakeSampler(),
    )
    assert len(checkpoint.quality_measurements) == 1

    missing_warmup = _artifact(config).model_copy(
        update={
            "cases": tuple(case for case in _artifact(config).cases if case.case_set != "warmup")
        }
    )
    with pytest.raises(ValueError, match="warm-up"):
        await run_level(
            arm="bf16",
            concurrency=1,
            artifact=missing_warmup,
            cases_sha256=HASH,
            config=config,
            profile="full",
            client=client,  # type: ignore[arg-type]
            sampler=FakeSampler(),
        )


@pytest.mark.asyncio
async def test_arm_checkpoints_resumes_and_completes_matrix(sandbox: Path) -> None:
    config = small_config(load_config())
    artifact = _artifact(config)
    run_path = sandbox / "run.json"
    bf16_client = FakeStreamClient()
    first = await run_arm(
        run_path=run_path,
        run_id=RUN_ID,
        arm="bf16",
        artifact=artifact,
        cases_sha256=HASH,
        config=config,
        profile="full",
        provenance=server(config, "bf16"),
        client=bf16_client,  # type: ignore[arg-type]
        sampler=FakeSampler(),
    )
    calls = len(bf16_client.calls)
    assert first.completed_at is None
    await run_arm(
        run_path=run_path,
        run_id=RUN_ID,
        arm="bf16",
        artifact=artifact,
        cases_sha256=HASH,
        config=config,
        profile="full",
        provenance=server(config, "bf16"),
        client=bf16_client,  # type: ignore[arg-type]
        sampler=FakeSampler(),
    )
    assert len(bf16_client.calls) == calls

    final = await run_arm(
        run_path=run_path,
        run_id=RUN_ID,
        arm="awq",
        artifact=artifact,
        cases_sha256=HASH,
        config=config,
        profile="full",
        provenance=server(config, "awq"),
        client=FakeStreamClient(),  # type: ignore[arg-type]
        sampler=FakeSampler(),
    )
    assert final.completed_at is not None
    assert len(load_run(run_path).levels) == 4


@pytest.mark.asyncio
async def test_resume_rejects_checkpoint_hash_and_server_drift(sandbox: Path) -> None:
    config, artifact, manifest = complete_benchmark(load_config())
    run_path = sandbox / "run.json"
    bad_level = manifest.levels["bf16:1"].model_copy(update={"cases_sha256": "b" * 64})
    write_run(
        run_path,
        manifest.model_copy(update={"completed_at": None, "levels": {"bf16:1": bad_level}}),
    )
    with pytest.raises(ValueError, match="case hash"):
        await run_arm(
            run_path=run_path,
            run_id=RUN_ID,
            arm="bf16",
            artifact=artifact,
            cases_sha256=manifest.cases_sha256,
            config=config,
            profile="full",
            provenance=server(config, "bf16"),
            client=FakeStreamClient(),  # type: ignore[arg-type]
            sampler=FakeSampler(),
        )

    drifted = server(config, "bf16").model_copy(update={"gpu_name": "different"})
    write_run(run_path, manifest.model_copy(update={"completed_at": None, "levels": {}}))
    with pytest.raises(ValueError, match="provenance drifted"):
        await run_arm(
            run_path=run_path,
            run_id=RUN_ID,
            arm="bf16",
            artifact=artifact,
            cases_sha256=manifest.cases_sha256,
            config=config,
            profile="full",
            provenance=drifted,
            client=FakeStreamClient(),  # type: ignore[arg-type]
            sampler=FakeSampler(),
        )
