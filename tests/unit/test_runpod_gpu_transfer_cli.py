"""Summary: RunPod SSH transfer, remote wrapper, and CLI dispatch tests.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- Git, SSH, SCP, API, and benchmark processes are replaced by provider-free fakes.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from runpod_gpu_fakes import GIT_SHA, PUBLIC_KEY, RUN_ID, FakeApi, pod, session, typed_api
from vllm_bench_fakes import HASH, complete_benchmark

import runpod_bench
import runpod_gpu
from lib.runpod_gpu.config import load_config
from lib.runpod_gpu.models import CleanupEvidence
from lib.runpod_gpu.session import write_session
from lib.runpod_gpu.transfer import export_session, sync_session
from lib.vllm_bench.config import load_config as load_vllm_config
from lib.vllm_bench.state import write_case_bundle


def _private_key(config, sandbox, monkeypatch) -> None:
    path = sandbox / "operator-key"
    path.write_text("synthetic-private-key")
    monkeypatch.setenv(config.ssh.private_key_path_env, str(path))


def test_sync_requires_confirmation_matching_git_config_and_token(sandbox, monkeypatch) -> None:
    config = load_config()
    vllm, artifact, _manifest = complete_benchmark(load_vllm_config())
    cases = sandbox / "cases-ibm-final-test-full.json"
    write_case_bundle(cases, artifact)
    write_session(config, sandbox, session(config))
    api = FakeApi(pod(config))
    with pytest.raises(PermissionError, match="explicit"):
        sync_session(
            config,
            vllm,
            typed_api(api),
            run_id=RUN_ID,
            cases_path=cases,
            repo_root=sandbox,
            confirmed=False,
        )
    monkeypatch.setattr("lib.runpod_gpu.transfer.git_commit", lambda _root: "b" * 40)
    with pytest.raises(ValueError, match="changed after Pod creation"):
        sync_session(
            config,
            vllm,
            typed_api(api),
            run_id=RUN_ID,
            cases_path=cases,
            repo_root=sandbox,
            confirmed=True,
        )
    monkeypatch.setattr("lib.runpod_gpu.transfer.git_commit", lambda _root: GIT_SHA)
    monkeypatch.delenv(vllm.server.api_key_env, raising=False)
    with pytest.raises(ValueError, match="VLLM_API_KEY is required"):
        sync_session(
            config,
            vllm,
            typed_api(api),
            run_id=RUN_ID,
            cases_path=cases,
            repo_root=sandbox,
            confirmed=True,
        )


def test_sync_streams_secret_only_on_stdin_and_records_case_hash(sandbox, monkeypatch) -> None:
    config = load_config()
    vllm, artifact, _manifest = complete_benchmark(load_vllm_config())
    cases = sandbox / "cases-ibm-final-test-full.json"
    cases_sha = write_case_bundle(cases, artifact)
    write_session(config, sandbox, session(config))
    api = FakeApi(pod(config))
    _private_key(config, sandbox, monkeypatch)
    monkeypatch.setenv(vllm.server.api_key_env, "synthetic-vllm-token")
    monkeypatch.setattr("lib.runpod_gpu.transfer.git_commit", lambda _root: GIT_SHA)
    ssh_calls: list[tuple[str, bytes | None]] = []
    process_calls = []
    monkeypatch.setattr(
        "lib.runpod_gpu.transfer._run_ssh",
        lambda _argv, command, input_bytes=None: ssh_calls.append((command, input_bytes)),
    )

    def run(command, **kwargs):
        process_calls.append((command, kwargs))
        output = b"synthetic-tar" if command[:2] == ("git", "archive") else None
        return subprocess.CompletedProcess(command, 0, stdout=output)

    monkeypatch.setattr("lib.runpod_gpu.transfer.subprocess.run", run)
    synced = sync_session(
        config,
        vllm,
        typed_api(api),
        run_id=RUN_ID,
        cases_path=cases,
        repo_root=sandbox,
        confirmed=True,
    )
    assert synced.cases_sha256 == cases_sha
    assert synced.synced_at is not None
    assert any(value == b"synthetic-vllm-token" for _, value in ssh_calls)
    assert all("synthetic-vllm-token" not in command for command, _ in ssh_calls)
    assert any(call[0][0] == "scp" for call in process_calls)


def test_export_refuses_unsynced_or_existing_and_validates_lineage(sandbox, monkeypatch) -> None:
    config = load_config()
    api = FakeApi(pod(config))
    write_session(config, sandbox, session(config))
    with pytest.raises(ValueError, match="must be synced"):
        export_session(config, typed_api(api), run_id=RUN_ID, repo_root=sandbox)

    synced = session(config, cases_sha256=HASH, cases_filename="cases-ibm-final-test-full.json")
    write_session(config, sandbox, synced)
    local_run = sandbox / ".local" / "vllm-bench" / RUN_ID
    local_run.mkdir(parents=True)
    with pytest.raises(ValueError, match="ambiguous overwrite"):
        export_session(config, typed_api(api), run_id=RUN_ID, repo_root=sandbox)

    local_run.rmdir()
    _private_key(config, sandbox, monkeypatch)
    _config, _artifact, manifest = complete_benchmark(load_vllm_config())
    manifest = manifest.model_copy(update={"run_id": RUN_ID, "cases_sha256": HASH})
    calls = []
    monkeypatch.setattr(
        "lib.runpod_gpu.transfer.subprocess.run",
        lambda command, **kwargs: (
            calls.append((command, kwargs)) or subprocess.CompletedProcess(command, 0)
        ),
    )
    monkeypatch.setattr("lib.runpod_gpu.transfer.load_run", lambda _path: manifest)
    exported = export_session(config, typed_api(api), run_id=RUN_ID, repo_root=sandbox)
    assert exported.exported_at is not None
    assert calls[0][0][0] == "scp"


def test_remote_wrapper_injects_process_runtime_from_mode_0600_file(sandbox, monkeypatch) -> None:
    runpod_config = load_config()
    vllm_config = load_vllm_config()
    token_path = sandbox / "vllm-token"
    token_path.write_text("synthetic-vllm-token")
    token_path.chmod(0o600)
    remote = runpod_config.remote.model_copy(update={"api_key_path": str(token_path)})
    monkeypatch.setattr(
        runpod_bench,
        "load_runpod_config",
        lambda _path: runpod_config.model_copy(update={"remote": remote}),
    )
    monkeypatch.setattr(runpod_bench, "load_vllm_config", lambda _path: vllm_config)
    runtime_values = []

    def dispatch(args) -> int:
        runtime_values.append(
            (
                os.environ[vllm_config.server.api_key_env],
                os.environ[vllm_config.server.image_digest_env],
                os.environ["VLLM_BENCH_RUNTIME"],
            )
        )
        return len(args or ())

    monkeypatch.delenv(vllm_config.server.api_key_env, raising=False)
    monkeypatch.delenv(vllm_config.server.image_digest_env, raising=False)
    monkeypatch.delenv("VLLM_BENCH_RUNTIME", raising=False)
    monkeypatch.setattr(runpod_bench.benchmark_vllm, "main", dispatch)
    assert runpod_bench.main(["validate"]) == 1
    assert runtime_values == [("synthetic-vllm-token", runpod_config.pod.image_digest, "process")]
    assert vllm_config.server.api_key_env not in os.environ
    assert vllm_config.server.image_digest_env not in os.environ
    assert "VLLM_BENCH_RUNTIME" not in os.environ
    token_path.chmod(0o644)
    with pytest.raises(ValueError, match="mode 0600"):
        runpod_bench.main(["validate"])


class _Client:
    def __enter__(self):
        return object()

    def __exit__(self, *_args):
        return None


def test_cli_dispatches_every_operator_stage(sandbox, monkeypatch) -> None:
    config = load_config()
    state = session(config)
    clean = CleanupEvidence(
        run_id=RUN_ID,
        pod_name=config.pod_name(RUN_ID),
        matching_pod_ids=(),
        matching_volume_ids=(),
        clean=True,
    )
    calls: list[str] = []
    monkeypatch.setattr(runpod_gpu, "load_config", lambda _path: config)
    monkeypatch.setattr(runpod_gpu, "_client", lambda _config: _Client())
    monkeypatch.setattr(runpod_gpu, "_plan", lambda *_args: clean)
    monkeypatch.setattr(runpod_gpu, "read_public_key", lambda _config: PUBLIC_KEY)
    monkeypatch.setattr(
        runpod_gpu, "create_session", lambda *_args, **_kwargs: calls.append("create") or state
    )
    monkeypatch.setattr(runpod_gpu, "pod_status", lambda *_args, **_kwargs: clean)
    monkeypatch.setattr(runpod_gpu, "ssh_argv", lambda *_args: ("ssh", "synthetic"))
    monkeypatch.setattr(runpod_gpu.os, "execvp", lambda *_args: calls.append("ssh"))
    monkeypatch.setattr(runpod_gpu, "load_vllm_config", lambda _path: load_vllm_config())
    monkeypatch.setattr(
        runpod_gpu, "sync_session", lambda *_args, **_kwargs: calls.append("sync") or state
    )
    monkeypatch.setattr(
        runpod_gpu, "start_session", lambda *_args, **_kwargs: calls.append("start")
    )
    monkeypatch.setattr(runpod_gpu, "stop_session", lambda *_args, **_kwargs: calls.append("stop"))
    monkeypatch.setattr(
        runpod_gpu, "export_session", lambda *_args, **_kwargs: calls.append("export") or state
    )
    monkeypatch.setattr(
        runpod_gpu, "delete_session", lambda *_args, **_kwargs: calls.append("delete") or state
    )
    monkeypatch.setattr(runpod_gpu, "verify_clean", lambda *_args, **_kwargs: clean)

    commands = (
        ["plan", "--run", RUN_ID],
        ["create", "--run", RUN_ID, "--confirm-create"],
        ["status", "--run", RUN_ID],
        ["ssh", "--run", RUN_ID],
        ["sync", "--run", RUN_ID, "--cases", str(sandbox / "cases"), "--confirm-sync"],
        ["start", "--run", RUN_ID, "--confirm-start"],
        ["stop", "--run", RUN_ID, "--confirm-stop"],
        ["export", "--run", RUN_ID],
        ["delete", "--run", RUN_ID, "--confirm-delete"],
        ["verify-clean", "--run", RUN_ID],
    )
    for command in commands:
        assert runpod_gpu.main(command) == 0
    assert calls == ["create", "ssh", "sync", "start", "stop", "export", "delete"]


def test_cli_client_plan_and_json_output_are_typed(monkeypatch, capsys) -> None:
    config = load_config()
    clean = CleanupEvidence(
        run_id=RUN_ID,
        pod_name=config.pod_name(RUN_ID),
        matching_pod_ids=(),
        matching_volume_ids=(),
        clean=True,
    )
    monkeypatch.setenv(config.api_key_env, "synthetic")
    monkeypatch.setattr(runpod_gpu, "RunpodApi", lambda **_kwargs: "client")
    assert runpod_gpu._client(config) == "client"
    monkeypatch.setattr(runpod_gpu, "load_budget_config", lambda *_args: object())
    monkeypatch.setattr(runpod_gpu, "read_gpu_inventory", lambda: ())
    monkeypatch.setattr(runpod_gpu, "build_plan", lambda *_args, **_kwargs: clean)
    args = type("Args", (), {"budget_config": Path("budget"), "run": RUN_ID})()
    assert runpod_gpu._plan(args, config) == clean
    runpod_gpu._print_model(clean)
    assert '"clean": true' in capsys.readouterr().out
