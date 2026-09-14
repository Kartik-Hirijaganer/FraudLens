"""Summary: Hosting-neutral NVIDIA, DCGM, and vLLM telemetry parsing and sampling.

Key classes:
- TelemetrySample: one timestamped GPU/KV-cache/queue observation.
- TelemetrySummary: aggregate utilization, memory, cache, and queue statistics.
- HostGpuFacts: GPU and driver identity captured for provenance.
- GpuSampler: async sampling protocol consumed by the load runner.
- CommandGpuSampler: nvidia-smi or DCGM command sampler with optional Prometheus enrichment.
- NullGpuSampler: explicit no-telemetry sampler for portable tests.

Key functions:
- percentile: linearly interpolate a deterministic percentile over finite values.
- parse_nvidia_smi: parse one query-csv observation.
- parse_dcgm: parse one DCGM Prometheus observation.
- parse_prometheus: extract vLLM cache and queue gauges.
- summarize: aggregate a benchmark telemetry window.
- build_sampler: construct the configured sampler.
- query_host_facts: read non-sensitive GPU and driver identity.

Notes:
- No command output is logged; only typed numeric samples and device/version strings persist.
"""

from __future__ import annotations

import asyncio
import math
import re
import subprocess
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from statistics import mean
from typing import Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from lib.vllm_bench.config import TelemetryConfig

_MODEL_CONFIG = ConfigDict(
    frozen=True, extra="forbid", alias_generator=to_camel, populate_by_name=True
)
_METRIC = re.compile(
    r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)(?:\{[^}]*\})?\s+(?P<value>-?[0-9.eE+]+)$"
)
_NVIDIA_TELEMETRY_COLUMNS = 3
_NVIDIA_IDENTITY_COLUMNS = 2


class TelemetrySample(BaseModel):
    """One timestamped GPU plus optional vLLM cache/queue observation."""

    model_config = _MODEL_CONFIG

    captured_at: datetime = Field(..., description="UTC sampling time.")
    gpu_utilization_pct: float | None = Field(
        default=None, ge=0, le=100, description="GPU busy percent."
    )
    memory_used_mib: float | None = Field(default=None, ge=0, description="Allocated GPU MiB.")
    memory_total_mib: float | None = Field(default=None, gt=0, description="Total GPU MiB.")
    kv_cache_usage_pct: float | None = Field(
        default=None, ge=0, le=100, description="KV cache use."
    )
    requests_running: float | None = Field(default=None, ge=0, description="Running requests.")
    requests_waiting: float | None = Field(default=None, ge=0, description="Queued requests.")


class TelemetrySummary(BaseModel):
    """Window aggregate used in performance reports and acceptance checks."""

    model_config = _MODEL_CONFIG

    samples: int = Field(..., ge=0, description="Total samples captured.")
    gpu_utilization_mean_pct: float | None = Field(default=None, description="Mean GPU use.")
    gpu_utilization_p95_pct: float | None = Field(default=None, description="p95 GPU use.")
    memory_peak_mib: float | None = Field(default=None, description="Peak used GPU memory.")
    kv_cache_peak_pct: float | None = Field(default=None, description="Peak KV-cache use.")
    queue_peak: float | None = Field(default=None, description="Peak waiting requests.")


class HostGpuFacts(BaseModel):
    """Observed device and driver identity for server provenance."""

    model_config = _MODEL_CONFIG

    gpu_name: str = Field(..., min_length=1, description="NVIDIA device name.")
    driver_version: str = Field(..., min_length=1, description="NVIDIA driver version.")


@runtime_checkable
class GpuSampler(Protocol):
    """Async hosting-neutral GPU sampler."""

    async def sample(self) -> TelemetrySample | None:
        """Return one observation or None when telemetry is explicitly disabled."""
        ...


def _float(value: str) -> float:
    """Parse nvidia-smi values after removing units and whitespace."""
    return float(re.sub(r"[^0-9.eE+-]", "", value))


def parse_nvidia_smi(text: str, *, captured_at: datetime | None = None) -> TelemetrySample:
    """Parse `utilization.gpu,memory.used,memory.total` query-csv output."""
    line = next((item.strip() for item in text.splitlines() if item.strip()), "")
    columns = [item.strip() for item in line.split(",")]
    if len(columns) != _NVIDIA_TELEMETRY_COLUMNS:
        raise ValueError("nvidia-smi telemetry must contain utilization, used, and total")
    return TelemetrySample(
        captured_at=captured_at or datetime.now(UTC),
        gpu_utilization_pct=_float(columns[0]),
        memory_used_mib=_float(columns[1]),
        memory_total_mib=_float(columns[2]),
    )


def _prometheus_values(text: str) -> dict[str, list[float]]:
    values: dict[str, list[float]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _METRIC.match(line)
        if match is not None:
            values.setdefault(match.group("name"), []).append(float(match.group("value")))
    return values


def parse_dcgm(text: str, *, captured_at: datetime | None = None) -> TelemetrySample:
    """Parse DCGM exporter gauges into one aggregate multi-device sample."""
    values = _prometheus_values(text)
    utilization = values.get("DCGM_FI_DEV_GPU_UTIL", [])
    used = values.get("DCGM_FI_DEV_FB_USED", [])
    free = values.get("DCGM_FI_DEV_FB_FREE", [])
    if not utilization or not used or len(used) != len(free):
        raise ValueError("DCGM telemetry is missing GPU utilization or framebuffer gauges")
    return TelemetrySample(
        captured_at=captured_at or datetime.now(UTC),
        gpu_utilization_pct=mean(utilization),
        memory_used_mib=sum(used),
        memory_total_mib=sum(used) + sum(free),
    )


def parse_prometheus(text: str) -> dict[str, float | None]:
    """Extract vLLM cache-use and scheduler queue gauges from Prometheus exposition."""
    values = _prometheus_values(text)

    def total(*names: str) -> float | None:
        observed = [value for name in names for value in values.get(name, [])]
        return sum(observed) if observed else None

    cache = total("vllm:gpu_cache_usage_perc", "vllm_gpu_cache_usage_perc")
    return {
        "kv_cache_usage_pct": cache * 100 if cache is not None and cache <= 1 else cache,
        "requests_running": total("vllm:num_requests_running", "vllm_num_requests_running"),
        "requests_waiting": total("vllm:num_requests_waiting", "vllm_num_requests_waiting"),
    }


def _values(samples: Sequence[TelemetrySample], field: str) -> list[float]:
    return [float(value) for sample in samples if (value := getattr(sample, field)) is not None]


def percentile(values: Sequence[float], quantile: float) -> float:
    """Return a linearly interpolated percentile over finite observations."""
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be between zero and one")
    ordered = sorted(value for value in values if math.isfinite(value))
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize(samples: Sequence[TelemetrySample]) -> TelemetrySummary:
    """Aggregate utilization, memory, cache, and queue statistics for one level."""
    utilization = _values(samples, "gpu_utilization_pct")
    memory = _values(samples, "memory_used_mib")
    cache = _values(samples, "kv_cache_usage_pct")
    queue = _values(samples, "requests_waiting")
    return TelemetrySummary(
        samples=len(samples),
        gpu_utilization_mean_pct=mean(utilization) if utilization else None,
        gpu_utilization_p95_pct=percentile(utilization, 0.95) if utilization else None,
        memory_peak_mib=max(memory) if memory else None,
        kv_cache_peak_pct=max(cache) if cache else None,
        queue_peak=max(queue) if queue else None,
    )


class NullGpuSampler:
    """Explicit sampler for GPU-free validation and fake-server tests."""

    async def sample(self) -> None:
        """Return no telemetry by design."""
        return None


class CommandGpuSampler:
    """Run an NVIDIA/DCGM command and optionally enrich it with vLLM metrics."""

    def __init__(
        self,
        *,
        command: tuple[str, ...],
        parser: Callable[..., TelemetrySample],
        prometheus_url: str | None,
        runner: Callable[[Sequence[str]], Awaitable[str]] | None = None,
        metrics_reader: Callable[[str], Awaitable[str]] | None = None,
    ) -> None:
        """Bind command, parser, and injectable IO seams."""
        self._command = command
        self._parser = parser
        self._prometheus_url = prometheus_url
        self._runner = runner or _run_command
        self._metrics_reader = metrics_reader or _read_url

    async def sample(self) -> TelemetrySample:
        """Capture one GPU observation and optional vLLM scheduler gauges."""
        captured_at = datetime.now(UTC)
        base = self._parser(await self._runner(self._command), captured_at=captured_at)
        if self._prometheus_url is None:
            return base
        extra = parse_prometheus(await self._metrics_reader(self._prometheus_url))
        return base.model_copy(update=extra)


async def _run_command(command: Sequence[str]) -> str:
    """Run one read-only telemetry command without shell interpolation."""

    def invoke() -> str:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        return completed.stdout

    return await asyncio.to_thread(invoke)


async def _read_url(url: str) -> str:
    """Read one local/private Prometheus endpoint."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


def build_sampler(config: TelemetryConfig) -> GpuSampler:
    """Construct the configured hosting-neutral sampler."""
    prefix = config.command_prefix
    if config.sampler == "none":
        return NullGpuSampler()
    if config.sampler == "dcgm":
        if config.dcgm_url is None:
            raise ValueError("telemetry.dcgm_url is required for the DCGM sampler")
        return CommandGpuSampler(
            command=(*prefix, "curl", "--fail", "--silent", config.dcgm_url),
            parser=parse_dcgm,
            prometheus_url=config.prometheus_url,
        )
    return CommandGpuSampler(
        command=(
            *prefix,
            "nvidia-smi",
            "--query-gpu=utilization.gpu,memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ),
        parser=parse_nvidia_smi,
        prometheus_url=config.prometheus_url,
    )


def query_host_facts(prefix: tuple[str, ...] = ()) -> HostGpuFacts:
    """Read the first GPU name and driver version for immutable provenance."""
    command = (
        *prefix,
        "nvidia-smi",
        "--query-gpu=name,driver_version",
        "--format=csv,noheader",
    )
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    line = next((item.strip() for item in completed.stdout.splitlines() if item.strip()), "")
    parts = [item.strip() for item in line.split(",")]
    if len(parts) != _NVIDIA_IDENTITY_COLUMNS or not all(parts):
        raise ValueError("nvidia-smi did not return GPU and driver identity")
    return HostGpuFacts(gpu_name=parts[0], driver_version=parts[1])
