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
    render_process_argv,
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
    assert bf16[bf16.index("--publish") + 1] == "127.0.0.1:8000:8000"
    assert bf16[bf16.index("--host") + 1] == "0.0.0.0"
    for option in ("--max-model-len", "--gpu-memory-utilization", "--max-num-seqs"):
        assert bf16[bf16.index(option) + 1] == awq[awq.index(option) + 1]


def test_process_argv_is_loopback_only_and_preserves_fairness() -> None:
    config = load_config()
    bf16 = render_process_argv(config, "bf16")
    awq = render_process_argv(config, "awq")
    assert bf16[:2] == ("vllm", "serve")
    assert bf16[bf16.index("--host") + 1] == "127.0.0.1"
    assert "--quantization" not in bf16
    assert awq[awq.index("--quantization") + 1] == "awq_marlin"


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

    process_log = sandbox / config.paths.output_dir / "server-startup.log"
    process_log.parent.mkdir(parents=True)
    process_log.write_text(_LOGS)
    monkeypatch.setenv("VLLM_BENCH_RUNTIME", "process")
    assert read_startup_logs(config, repo_root=sandbox) == _LOGS


def test_local_lifecycle_and_image_digest_are_checked(monkeypatch) -> None:
    config = load_config()
    calls = []
    monkeypatch.delenv("VLLM_BENCH_RUNTIME", raising=False)
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


def test_process_lifecycle_records_identity_and_stops_matching_group(sandbox, monkeypatch) -> None:
    config = load_config()
    digest = f"sha256:{'a' * 64}"
    popen_calls = []
    signals = []

    class Process:
        pid = 4321

    def launch(command, **kwargs):
        popen_calls.append((command, kwargs))
        return Process()

    monkeypatch.setenv("VLLM_BENCH_RUNTIME", "process")
    monkeypatch.setenv(config.server.api_key_env, "test-key")
    monkeypatch.setenv(config.server.image_digest_env, digest)
    monkeypatch.setattr("lib.vllm_bench.server.subprocess.Popen", launch)
    serve(config, "bf16", repo_root=sandbox)

    state_path = sandbox / config.paths.output_dir / "server-process.json"
    assert state_path.is_file()
    assert popen_calls[0][0][:2] == ("vllm", "serve")
    assert popen_calls[0][1]["start_new_session"] is True

    monkeypatch.setattr("lib.vllm_bench.server._process_exists", lambda _pid: True)
    monkeypatch.setattr(
        "lib.vllm_bench.server._command_output",
        lambda _command: f"vllm serve --model {config.arms['bf16'].model}",
    )
    monkeypatch.setattr("lib.vllm_bench.server.os.killpg", lambda *args: signals.append(args))
    stop(config, repo_root=sandbox)
    assert signals == [(4321, 15)]
    assert not state_path.exists()


def test_process_runtime_refuses_missing_digest_duplicate_and_pid_mismatch(
    sandbox, monkeypatch
) -> None:
    config = load_config()
    digest = f"sha256:{'b' * 64}"
    monkeypatch.setenv("VLLM_BENCH_RUNTIME", "process")
    monkeypatch.setenv(config.server.api_key_env, "test-key")
    monkeypatch.delenv(config.server.image_digest_env, raising=False)
    with pytest.raises(ValueError, match="required for process"):
        serve(config, "bf16", repo_root=sandbox)

    monkeypatch.setenv(config.server.image_digest_env, digest)
    monkeypatch.setattr("lib.vllm_bench.server.subprocess.Popen", lambda *_args, **_kwargs: None)
    state_path = sandbox / config.paths.output_dir / "server-process.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        f'{{"pid":4321,"arm":"bf16","model":"Qwen/Qwen2.5-7B-Instruct","image_digest":"{digest}"}}'
    )
    monkeypatch.setattr("lib.vllm_bench.server._process_exists", lambda _pid: True)
    with pytest.raises(ValueError, match="already running"):
        serve(config, "bf16", repo_root=sandbox)
    monkeypatch.setattr("lib.vllm_bench.server._command_output", lambda _command: "sleep 100")
    with pytest.raises(ValueError, match="does not match"):
        stop(config, repo_root=sandbox)


def test_runtime_selector_rejects_unknown_value(monkeypatch) -> None:
    config = load_config()
    monkeypatch.setenv("VLLM_BENCH_RUNTIME", "containerd")
    monkeypatch.setenv(config.server.api_key_env, "test-key")
    with pytest.raises(ValueError, match="must be docker or process"):
        serve(config, "bf16")


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
            host_key=config.cost.default_host,
            purchase_option="spot",
            startup_logs=_LOGS,
            digest=digest,
            gpu=gpu,
        )
