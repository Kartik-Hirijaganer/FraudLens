"""Summary: Comparable vLLM server argv rendering, startup-log parsing, and provenance capture.

Key classes:
- StartupMetrics: deduplicated model-memory, KV-cache, and maximum-concurrency observations.

Key functions:
- render_docker_argv: build the exact shell-free Docker argv for one arm.
- parse_startup_logs: extract required startup evidence.
- read_startup_logs: read Docker, Kubernetes, or file logs through argv-only commands.
- serve: start one arm after validating its API-key environment name is populated.
- stop: stop the configured local benchmark container.
- image_digest: resolve the locally installed image digest.
- server_provenance: bind startup, image, GPU, host, and price observations.

Notes:
- Command construction never interpolates secret values or invokes a shell.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from lib.vllm_bench.config import ArmName, PurchaseOption, VllmBenchConfig
from lib.vllm_bench.state import ServerProvenance, validate_restart_memory
from lib.vllm_bench.telemetry import HostGpuFacts, query_host_facts

_MEMORY = re.compile(r"Model loading took\s+(?P<value>[0-9.]+)\s+GiB", re.IGNORECASE)
_KV_CACHE = re.compile(r"GPU KV cache size:\s*(?P<value>[0-9,]+)\s+tokens", re.IGNORECASE)
_CONCURRENCY = re.compile(r"Maximum concurrency[^:]*:\s*(?P<value>[0-9.]+)x", re.IGNORECASE)
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_RESTART_MEMORY_TOLERANCE = 0.01


class StartupMetrics(BaseModel):
    """Deduplicated vLLM startup evidence required for fair comparison."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    weight_memory_gib: float = Field(..., gt=0, description="Parsed model-weight allocation.")
    kv_cache_tokens: int = Field(..., gt=0, description="Parsed GPU KV-cache token capacity.")
    maximum_concurrency: float = Field(..., gt=0, description="Parsed scheduler concurrency.")


def render_docker_argv(
    config: VllmBenchConfig, arm: ArmName, *, digest: str | None = None
) -> tuple[str, ...]:
    """Build the exact comparable Docker argv for one model arm."""
    if digest is not None and _DIGEST.fullmatch(digest) is None:
        raise ValueError("image digest must be a sha256 repository digest")
    selected = config.arms[arm]
    server = config.server
    image_reference = f"{server.image}@{digest}" if digest else f"{server.image}:{server.image_tag}"
    arguments = [
        "docker",
        "run",
        "--detach",
        "--rm",
        "--gpus",
        "all",
        "--ipc",
        "host",
        "--name",
        server.container_name,
        "--publish",
        f"{server.port}:{server.port}",
        "--env",
        server.api_key_env,
        image_reference,
        "--model",
        selected.model,
        "--revision",
        selected.revision,
        "--tokenizer",
        selected.tokenizer,
        "--tokenizer-revision",
        selected.tokenizer_revision,
        "--dtype",
        selected.dtype,
        "--port",
        str(server.port),
        "--max-model-len",
        str(server.max_model_len),
        "--gpu-memory-utilization",
        str(server.gpu_memory_utilization),
        "--max-num-seqs",
        str(server.max_num_seqs),
        "--seed",
        str(config.seed),
        "--no-enable-prefix-caching",
    ]
    if selected.quantization is not None:
        arguments.extend(("--quantization", selected.quantization))
    arguments.extend(server.extra_args)
    return tuple(arguments)


def _one_consistent(values: list[float], label: str) -> float:
    """Deduplicate repeated worker log lines while rejecting inconsistent allocations."""
    if not values:
        raise ValueError(f"vLLM startup logs are missing {label}")
    first = values[0]
    for value in values[1:]:
        validate_restart_memory(first, value)
    return first


def parse_startup_logs(text: str) -> StartupMetrics:
    """Extract mandatory model allocation, GPU KV-cache, and maximum concurrency."""
    memory = [float(match.group("value")) for match in _MEMORY.finditer(text)]
    caches = [int(match.group("value").replace(",", "")) for match in _KV_CACHE.finditer(text)]
    concurrency = [float(match.group("value")) for match in _CONCURRENCY.finditer(text)]
    if not caches or not concurrency:
        raise ValueError("vLLM startup logs are missing KV-cache or concurrency evidence")
    if len(set(caches)) != 1 or any(
        abs(item - concurrency[0]) / concurrency[0] > _RESTART_MEMORY_TOLERANCE
        for item in concurrency[1:]
    ):
        raise ValueError("vLLM startup evidence is inconsistent across repeated log lines")
    return StartupMetrics(
        weight_memory_gib=_one_consistent(memory, "model weight memory"),
        kv_cache_tokens=caches[0],
        maximum_concurrency=concurrency[0],
    )


def _command_output(command: Sequence[str]) -> str:
    """Run a read-only command and return stdout without logging it."""
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout


def read_startup_logs(config: VllmBenchConfig, *, repo_root: Path) -> str:
    """Read configured startup logs from a file, Docker container, or Kubernetes pod."""
    source = config.server.log_source
    if source.kind == "file":
        path = Path(source.target)
        target = path if path.is_absolute() else repo_root / path
        return target.read_text(encoding="utf-8")
    if source.kind == "docker":
        return _command_output((*source.command_prefix, "docker", "logs", source.target))
    return _command_output((*source.command_prefix, "kubectl", "logs", source.target))


def serve(config: VllmBenchConfig, arm: ArmName) -> subprocess.CompletedProcess[str]:
    """Start one local vLLM arm without exposing the injected API-key value."""
    if not os.environ.get(config.server.api_key_env, "").strip():
        raise ValueError(f"{config.server.api_key_env} is required")
    digest = os.environ.get(config.server.image_digest_env) or image_digest(config)
    return subprocess.run(render_docker_argv(config, arm, digest=digest), check=True, text=True)


def stop(config: VllmBenchConfig) -> None:
    """Stop the configured local benchmark container."""
    subprocess.run(("docker", "stop", config.server.container_name), check=True)


def image_digest(config: VllmBenchConfig) -> str:
    """Resolve the first immutable repository digest for the pinned local image."""
    image = f"{config.server.image}:{config.server.image_tag}"
    output = _command_output(
        ("docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}")
    )
    matches = re.findall(r"@(?P<digest>sha256:[0-9a-f]{64})", output)
    if not matches:
        raise ValueError("pinned vLLM image has no locally resolved repository digest")
    return str(matches[0])


def server_provenance(  # noqa: PLR0913 - provenance binds explicit observed evidence.
    config: VllmBenchConfig,
    *,
    arm: ArmName,
    host_key: str,
    purchase_option: PurchaseOption,
    startup_logs: str,
    digest: str,
    gpu: HostGpuFacts | None = None,
) -> ServerProvenance:
    """Bind every required immutable server, hardware, and rate observation."""
    if _DIGEST.fullmatch(digest) is None:
        raise ValueError("image digest must be a sha256 repository digest")
    host = config.cost.hosts.get(host_key)
    if host is None:
        raise ValueError(f"unknown cost host '{host_key}'")
    rate = host.prices.get(purchase_option)
    if rate is None:
        raise ValueError(f"host '{host_key}' has no {purchase_option} price")
    selected = config.arms[arm]
    startup = parse_startup_logs(startup_logs)
    facts = gpu or query_host_facts(config.telemetry.command_prefix)
    return ServerProvenance(
        arm=arm,
        model=selected.model,
        model_revision=selected.revision,
        tokenizer=selected.tokenizer,
        tokenizer_revision=selected.tokenizer_revision,
        image=f"{config.server.image}:{config.server.image_tag}",
        image_digest=digest,
        vllm_version=config.server.image_tag,
        gpu_name=facts.gpu_name,
        driver_version=facts.driver_version,
        host_key=host_key,
        provider=host.provider,
        sku=host.sku,
        region=host.region,
        purchase_option=purchase_option,
        hourly_rate_usd=float(rate),
        price_source_url=host.price_source_url,
        price_verified_at=host.price_verified_at.isoformat(),
        weight_memory_gib=startup.weight_memory_gib,
        safetensors_total_gib=float(selected.safetensors_total_gib),
        kv_cache_tokens=startup.kv_cache_tokens,
        maximum_concurrency=startup.maximum_concurrency,
    )
