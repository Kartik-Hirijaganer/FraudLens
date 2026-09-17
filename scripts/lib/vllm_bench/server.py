"""Summary: Comparable vLLM server lifecycle, startup parsing, and provenance capture.

Key classes:
- ProcessServerState: local identity for a directly launched vLLM process.
- StartupMetrics: deduplicated model-memory, KV-cache, and maximum-concurrency observations.

Key functions:
- render_docker_argv: build the exact shell-free Docker argv for one arm.
- render_process_argv: build the exact shell-free in-container vLLM argv for one arm.
- parse_startup_logs: extract required startup evidence.
- read_startup_logs: read Docker, Kubernetes, or file logs through argv-only commands.
- serve: start one arm after validating its API-key environment name is populated.
- stop: stop the configured Docker container or direct vLLM process.
- image_digest: resolve the locally installed image digest.
- server_provenance: bind startup, image, GPU, host, and price observations.

Notes:
- Command construction never interpolates secret values or invokes a shell.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from lib.vllm_bench.config import ArmName, PurchaseOption, VllmBenchConfig
from lib.vllm_bench.state import ServerProvenance, validate_restart_memory
from lib.vllm_bench.telemetry import HostGpuFacts, query_host_facts

_MEMORY = re.compile(r"Model loading took\s+(?P<value>[0-9.]+)\s+GiB", re.IGNORECASE)
_KV_CACHE = re.compile(r"GPU KV cache size:\s*(?P<value>[0-9,]+)\s+tokens", re.IGNORECASE)
_CONCURRENCY = re.compile(r"Maximum concurrency[^:]*:\s*(?P<value>[0-9.]+)x", re.IGNORECASE)
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_RESTART_MEMORY_TOLERANCE = 0.01
_RUNTIME_ENV = "VLLM_BENCH_RUNTIME"
_PROCESS_STATE_NAME = "server-process.json"
_PROCESS_LOG_NAME = "server-startup.log"

ServerRuntime = Literal["docker", "process"]


class ProcessServerState(BaseModel):
    """Identity needed to stop a directly launched vLLM process safely."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pid: int = Field(..., gt=0, description="Process-group leader PID.")
    arm: ArmName = Field(..., description="Model arm served by the process.")
    model: str = Field(..., min_length=1, description="Expected model in the process argv.")
    image_digest: str = Field(..., pattern=_DIGEST.pattern, description="Runtime image digest.")


class StartupMetrics(BaseModel):
    """Deduplicated vLLM startup evidence required for fair comparison."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    weight_memory_gib: float = Field(..., gt=0, description="Parsed model-weight allocation.")
    kv_cache_tokens: int = Field(..., gt=0, description="Parsed GPU KV-cache token capacity.")
    maximum_concurrency: float = Field(..., gt=0, description="Parsed scheduler concurrency.")


def _runtime() -> ServerRuntime:
    """Resolve the explicit runtime selector, defaulting to the local Docker path."""
    value = os.environ.get(_RUNTIME_ENV, "docker")
    if value not in {"docker", "process"}:
        raise ValueError(f"{_RUNTIME_ENV} must be docker or process")
    return cast(ServerRuntime, value)


def _server_arguments(config: VllmBenchConfig, arm: ArmName, *, host: str) -> tuple[str, ...]:
    """Render the controls shared by Docker and direct-process runtimes."""
    selected = config.arms[arm]
    server = config.server
    arguments = [
        selected.model,
        "--revision",
        selected.revision,
        "--tokenizer",
        selected.tokenizer,
        "--tokenizer-revision",
        selected.tokenizer_revision,
        "--dtype",
        selected.dtype,
        "--host",
        host,
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


def render_docker_argv(
    config: VllmBenchConfig, arm: ArmName, *, digest: str | None = None
) -> tuple[str, ...]:
    """Build the exact comparable Docker argv for one model arm."""
    if digest is not None and _DIGEST.fullmatch(digest) is None:
        raise ValueError("image digest must be a sha256 repository digest")
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
        f"{server.docker_publish_host}:{server.port}:{server.port}",
        "--env",
        server.api_key_env,
        image_reference,
    ]
    arguments.extend(_server_arguments(config, arm, host=str(server.docker_bind_host)))
    return tuple(arguments)


def render_process_argv(config: VllmBenchConfig, arm: ArmName) -> tuple[str, ...]:
    """Build the direct vLLM argv used from inside a GPU container."""
    return (
        "vllm",
        "serve",
        *_server_arguments(config, arm, host=str(config.server.process_bind_host)),
    )


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


def read_startup_logs(
    config: VllmBenchConfig, *, repo_root: Path, command_prefix: Sequence[str] = ()
) -> str:
    """Read startup logs locally or through one runtime-only remote-command prefix."""
    if _runtime() == "process":
        path = repo_root / config.paths.output_dir / _PROCESS_LOG_NAME
        if command_prefix:
            return _command_output((*command_prefix, "cat", str(path)))
        return path.read_text(encoding="utf-8")
    source = config.server.log_source
    if source.kind == "file":
        path = Path(source.target)
        target = path if path.is_absolute() else repo_root / path
        return target.read_text(encoding="utf-8")
    if source.kind == "docker":
        return _command_output((*source.command_prefix, "docker", "logs", source.target))
    return _command_output((*source.command_prefix, "kubectl", "logs", source.target))


def _process_paths(config: VllmBenchConfig, repo_root: Path) -> tuple[Path, Path]:
    """Return the gitignored process-state and startup-log paths."""
    root = repo_root / config.paths.output_dir
    return root / _PROCESS_STATE_NAME, root / _PROCESS_LOG_NAME


def _load_process_state(path: Path) -> ProcessServerState:
    """Load a strict process identity from gitignored state."""
    return ProcessServerState.model_validate_json(path.read_text(encoding="utf-8"))


def _write_process_state(path: Path, state: ProcessServerState) -> None:
    """Atomically persist non-secret process identity."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(".tmp")
    staging.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(staging, path)


def _process_exists(pid: int) -> bool:
    """Return whether a process currently owns the recorded PID."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _serve_process(config: VllmBenchConfig, arm: ArmName, digest: str, *, repo_root: Path) -> None:
    """Launch vLLM as a detached process group and record its non-secret identity."""
    state_path, log_path = _process_paths(config, repo_root)
    if state_path.exists():
        state = _load_process_state(state_path)
        if _process_exists(state.pid):
            raise ValueError("a managed vLLM process is already running")
        state_path.unlink()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("wb") as logs:
        process = subprocess.Popen(
            render_process_argv(config, arm),
            stdout=logs,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    _write_process_state(
        state_path,
        ProcessServerState(
            pid=process.pid,
            arm=arm,
            model=config.arms[arm].model,
            image_digest=digest,
        ),
    )


def serve(config: VllmBenchConfig, arm: ArmName, *, repo_root: Path | None = None) -> None:
    """Start one vLLM arm without exposing the injected API-key value."""
    if not os.environ.get(config.server.api_key_env, "").strip():
        raise ValueError(f"{config.server.api_key_env} is required")
    runtime = _runtime()
    digest = os.environ.get(config.server.image_digest_env)
    if runtime == "process" and not digest:
        raise ValueError(f"{config.server.image_digest_env} is required for process runtime")
    resolved_digest = digest or image_digest(config)
    if _DIGEST.fullmatch(resolved_digest) is None:
        raise ValueError("image digest must be a sha256 repository digest")
    if runtime == "process":
        _serve_process(config, arm, resolved_digest, repo_root=repo_root or Path.cwd())
        return
    subprocess.run(render_docker_argv(config, arm, digest=resolved_digest), check=True, text=True)


def _stop_process(config: VllmBenchConfig, *, repo_root: Path) -> None:
    """Stop only the process group matching the recorded vLLM model identity."""
    state_path, _log_path = _process_paths(config, repo_root)
    state = _load_process_state(state_path)
    if not _process_exists(state.pid):
        state_path.unlink()
        return
    command = _command_output(("ps", "-p", str(state.pid), "-o", "command="))
    if "vllm serve" not in command or state.model not in command:
        raise ValueError("recorded PID does not match the managed vLLM process")
    os.killpg(state.pid, signal.SIGTERM)
    state_path.unlink()


def stop(config: VllmBenchConfig, *, repo_root: Path | None = None) -> None:
    """Stop the configured Docker container or direct vLLM process."""
    if _runtime() == "process":
        _stop_process(config, repo_root=repo_root or Path.cwd())
        return
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
