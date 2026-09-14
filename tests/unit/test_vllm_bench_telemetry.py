"""Summary: Hosting-neutral GPU, DCGM, Prometheus, and sampler tests.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- Command and HTTP seams are injected; tests never inspect a real GPU.
"""

from __future__ import annotations

import subprocess

import pytest
from vllm_bench_fakes import NOW, telemetry

from lib.vllm_bench.config import load_config
from lib.vllm_bench.telemetry import (
    CommandGpuSampler,
    NullGpuSampler,
    _run_command,
    build_sampler,
    parse_dcgm,
    parse_nvidia_smi,
    parse_prometheus,
    query_host_facts,
    summarize,
)


def test_nvidia_dcgm_and_vllm_metrics_parsing() -> None:
    sample = parse_nvidia_smi("75 %, 12000 MiB, 24576 MiB", captured_at=NOW)
    assert sample.gpu_utilization_pct == 75
    assert sample.memory_total_mib == 24576
    with pytest.raises(ValueError, match="utilization"):
        parse_nvidia_smi("bad")

    dcgm = parse_dcgm(
        """
        # HELP ignored
        DCGM_FI_DEV_GPU_UTIL{gpu="0"} 50
        DCGM_FI_DEV_GPU_UTIL{gpu="1"} 70
        DCGM_FI_DEV_FB_USED{gpu="0"} 100
        DCGM_FI_DEV_FB_USED{gpu="1"} 200
        DCGM_FI_DEV_FB_FREE{gpu="0"} 900
        DCGM_FI_DEV_FB_FREE{gpu="1"} 800
        """,
        captured_at=NOW,
    )
    assert dcgm.gpu_utilization_pct == 60
    assert dcgm.memory_used_mib == 300
    with pytest.raises(ValueError, match="missing"):
        parse_dcgm("DCGM_FI_DEV_GPU_UTIL 10")

    metrics = parse_prometheus(
        "vllm:gpu_cache_usage_perc 0.5\n"
        "vllm:num_requests_running 2\n"
        "vllm:num_requests_waiting 3\ninvalid line"
    )
    assert metrics == {
        "kv_cache_usage_pct": 50,
        "requests_running": 2,
        "requests_waiting": 3,
    }


def test_telemetry_window_summary_handles_complete_and_empty_samples() -> None:
    full = summarize((telemetry(), telemetry().model_copy(update={"gpu_utilization_pct": 90})))
    assert full.samples == 2
    assert full.gpu_utilization_mean_pct == 82.5
    assert full.gpu_utilization_p95_pct == pytest.approx(89.25)
    assert full.memory_peak_mib == 12_000
    assert full.kv_cache_peak_pct == 40
    assert full.queue_peak == 0
    assert summarize(()).gpu_utilization_mean_pct is None


@pytest.mark.asyncio
async def test_command_sampler_enriches_gpu_observation() -> None:
    async def runner(_command) -> str:
        return "50, 100, 1000"

    async def metrics(_url: str) -> str:
        return "vllm_gpu_cache_usage_perc 0.25\nvllm_num_requests_waiting 4"

    sampler = CommandGpuSampler(
        command=("nvidia-smi",),
        parser=parse_nvidia_smi,
        prometheus_url="http://127.0.0.1/metrics",
        runner=runner,
        metrics_reader=metrics,
    )
    sample = await sampler.sample()
    assert sample.kv_cache_usage_pct == 25
    assert sample.requests_waiting == 4


@pytest.mark.asyncio
async def test_sampler_factory_covers_none_nvidia_and_dcgm() -> None:
    config = load_config().telemetry
    assert await NullGpuSampler().sample() is None
    assert isinstance(build_sampler(config.model_copy(update={"sampler": "none"})), NullGpuSampler)
    nvidia = build_sampler(config)
    assert isinstance(nvidia, CommandGpuSampler)
    dcgm = build_sampler(config.model_copy(update={"sampler": "dcgm"}))
    assert isinstance(dcgm, CommandGpuSampler)
    with pytest.raises(ValueError, match="dcgm_url"):
        build_sampler(config.model_copy(update={"sampler": "dcgm", "dcgm_url": None}))


@pytest.mark.asyncio
async def test_command_runner_and_host_fact_query_are_shell_free(monkeypatch) -> None:
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        identity = any("name,driver_version" in str(part) for part in command)
        output = "Synthetic GPU, 555.1" if identity else "10,20,30"
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    assert await _run_command(("nvidia-smi",)) == "10,20,30"
    facts = query_host_facts(("ssh", "host"))
    assert facts.gpu_name == "Synthetic GPU"
    assert calls[0][1]["check"] is True
    with pytest.raises(ValueError, match="GPU and driver"):
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="bad"),
        )
        query_host_facts()
