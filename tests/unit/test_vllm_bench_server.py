"""Summary: vLLM server argv, startup evidence, lifecycle, and provenance tests.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- All Docker, Kubernetes, and GPU commands are intercepted.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lib.vllm_bench.config import load_config
from lib.vllm_bench.server import (
    image_digest,
    parse_startup_logs,
    read_startup_logs,
    render_docker_argv,
    serve,
    server_provenance,
    stop,
)
from lib.vllm_bench.telemetry import HostGpuFacts

_LOGS = """
Model loading took 14.0 GiB
GPU KV cache size: 12,345 tokens
Maximum concurrency for 8192 tokens per request: 1.50x
"""


def test_docker_argv_pins_fair_controls_and_arm_difference() -> None:
    config = load_config()
    bf16 = render_docker_argv(config, "bf16")
    awq = render_docker_argv(config, "awq")
    assert bf16[:4] == ("docker", "run", "--detach", "--rm")
    assert "--no-enable-prefix-caching" in bf16
    assert "--quantization" not in bf16
    assert awq[awq.index("--quantization") + 1] == "awq_marlin"
    digest = f"sha256:{'a' * 64}"
    assert f"{config.server.image}@{digest}" in render_docker_argv(config, "bf16", digest=digest)
    with pytest.raises(ValueError, match="digest"):
        render_docker_argv(config, "bf16", digest="latest")
    for option in ("--max-model-len", "--gpu-memory-utilization", "--max-num-seqs"):
        assert bf16[bf16.index(option) + 1] == awq[awq.index(option) + 1]


def test_startup_log_parser_deduplicates_and_rejects_missing_or_drifted() -> None:
    metrics = parse_startup_logs(_LOGS + _LOGS)
    assert metrics.weight_memory_gib == 14
    assert metrics.kv_cache_tokens == 12345
    assert metrics.maximum_concurrency == 1.5
    with pytest.raises(ValueError, match="missing KV-cache"):
        parse_startup_logs("Model loading took 1 GiB")
    with pytest.raises(ValueError, match="inconsistent"):
        parse_startup_logs(_LOGS + _LOGS.replace("12,345", "12,346"))
    with pytest.raises(ValueError, match="one percent"):
        parse_startup_logs(_LOGS + _LOGS.replace("14.0", "15.0"))


def test_log_sources_cover_file_docker_and_kubectl(sandbox: Path, monkeypatch) -> None:
    config = load_config()
    log = sandbox / "startup.log"
    log.write_text(_LOGS)
    file_config = config.model_copy(
        update={
            "server": config.server.model_copy(
                update={
                    "log_source": config.server.log_source.model_copy(
                        update={"kind": "file", "target": str(log)}
                    )
                }
            )
        }
    )
    assert read_startup_logs(file_config, repo_root=Path.cwd()) == _LOGS

    def render_command(command) -> str:
        return " ".join(command)

    monkeypatch.setattr("lib.vllm_bench.server._command_output", render_command)
    assert "docker logs" in read_startup_logs(config, repo_root=Path.cwd())
    kube = config.model_copy(
        update={
            "server": config.server.model_copy(
                update={
                    "log_source": config.server.log_source.model_copy(update={"kind": "kubectl"})
                }
            )
        }
    )
    assert "kubectl logs" in read_startup_logs(kube, repo_root=Path.cwd())


def test_local_lifecycle_and_image_digest_are_checked(monkeypatch) -> None:
    config = load_config()
    calls = []
    monkeypatch.delenv(config.server.api_key_env, raising=False)
    with pytest.raises(ValueError, match="required"):
        serve(config, "bf16")
    monkeypatch.setenv(config.server.api_key_env, "test-key")
    monkeypatch.setenv(config.server.image_digest_env, f"sha256:{'a' * 64}")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: (
            calls.append((command, kwargs)) or subprocess.CompletedProcess(command, 0, stdout="")
        ),
    )
    serve(config, "bf16")
    stop(config)
    assert calls[0][0][:2] == ("docker", "run")
    assert calls[1][0][:2] == ("docker", "stop")

    digest = f"sha256:{'c' * 64}"
    monkeypatch.setattr("lib.vllm_bench.server._command_output", lambda _command: f'["x@{digest}"]')
    assert image_digest(config) == digest
    monkeypatch.setattr("lib.vllm_bench.server._command_output", lambda _command: "[]")
    with pytest.raises(ValueError, match="no locally resolved"):
        image_digest(config)


def test_provenance_binds_host_gpu_price_and_startup_evidence() -> None:
    config = load_config()
    gpu = HostGpuFacts(gpu_name="Synthetic GPU", driver_version="555")
    digest = f"sha256:{'d' * 64}"
    provenance = server_provenance(
        config,
        arm="bf16",
        host_key=config.cost.default_host,
        purchase_option="pay_as_you_go",
        startup_logs=_LOGS,
        digest=digest,
        gpu=gpu,
    )
    assert provenance.weight_memory_gib == 14
    assert provenance.gpu_name == "Synthetic GPU"
    with pytest.raises(ValueError, match="digest"):
        server_provenance(
            config,
            arm="bf16",
            host_key=config.cost.default_host,
            purchase_option="pay_as_you_go",
            startup_logs=_LOGS,
            digest="latest",
            gpu=gpu,
        )
    with pytest.raises(ValueError, match="unknown cost host"):
        server_provenance(
            config,
            arm="bf16",
            host_key="missing",
            purchase_option="pay_as_you_go",
            startup_logs=_LOGS,
            digest=digest,
            gpu=gpu,
        )
    with pytest.raises(ValueError, match="has no spot"):
        server_provenance(
            config,
            arm="bf16",
            host_key="aws-g5-xlarge",
            purchase_option="spot",
            startup_logs=_LOGS,
            digest=digest,
            gpu=gpu,
        )
