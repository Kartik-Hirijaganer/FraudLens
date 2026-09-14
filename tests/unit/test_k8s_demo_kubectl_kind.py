"""Behavioral tests for Kubernetes context guards, typed parsers, and local kind lifecycle."""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from lib.k8s_demo import kubectl as kubectl_module
from lib.k8s_demo.config import load_config
from lib.k8s_demo.kind import KindOperator
from lib.k8s_demo.kubectl import (
    CommandError,
    CommandResult,
    Kubectl,
    resolve_tool,
    run_command,
)


class FakeRunner:
    """Injectable command runner recording argv while serving deterministic responses."""

    def __init__(self, responder: Callable[[list[str]], CommandResult]) -> None:
        self.responder = responder
        self.calls: list[list[str]] = []
        self.inputs: list[str | None] = []

    def __call__(
        self,
        command: Sequence[str],
        *,
        input_text: str | None = None,
        check: bool = True,
        timeout: float | None = None,
    ) -> CommandResult:
        del timeout
        argv = list(command)
        self.calls.append(argv)
        self.inputs.append(input_text)
        result = self.responder(argv)
        if check and result.returncode:
            raise CommandError("fake command failed")
        return result


def _result(stdout: str = "", returncode: int = 0) -> CommandResult:
    return CommandResult(returncode=returncode, stdout=stdout, stderr="")


def test_real_command_runner_and_tool_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run_command([sys.executable, "-c", "print('ok')"]).stdout == "ok\n"
    with pytest.raises(CommandError, match="command failed"):
        run_command([sys.executable, "-c", "raise SystemExit(3)"])
    monkeypatch.setattr(kubectl_module.shutil, "which", lambda _name: None)
    monkeypatch.setattr(kubectl_module, "REPO_ROOT", tmp_path)
    tools = tmp_path / ".local/tools"
    tools.mkdir(parents=True)
    cached = tools / "example"
    cached.touch()
    assert resolve_tool("example") == str(cached)
    with pytest.raises(CommandError, match="unavailable"):
        resolve_tool("absent")


def test_context_gates_allow_exact_kind_and_confirmed_non_kind() -> None:
    config = load_config()
    context = config.context
    runner = FakeRunner(lambda _argv: _result(f"{context}\n"))
    kubectl = Kubectl(config, runner=runner, binary="kubectl")
    kubectl.assert_mutation_allowed(platform="kind")
    with pytest.raises(CommandError, match="confirmation"):
        kubectl.assert_mutation_allowed(platform="aks")

    aks_runner = FakeRunner(lambda _argv: _result("aks-demo\n"))
    aks = Kubectl(config, runner=aks_runner, binary="kubectl")
    aks.assert_mutation_allowed(platform="aks", confirmed=True)
    empty = Kubectl(
        config,
        runner=FakeRunner(lambda _argv: _result(returncode=1)),
        binary="kubectl",
    )
    with pytest.raises(CommandError, match="<unset>"):
        empty.assert_mutation_allowed(platform="kind")
    with pytest.raises(CommandError, match="non-kind"):
        empty.assert_mutation_allowed(platform="aks", confirmed=True)


def test_typed_hpa_deployment_and_worker_parsers() -> None:
    config = load_config()
    restart_reads = 0

    def respond(argv: list[str]) -> CommandResult:  # noqa: PLR0911 - command fake dispatch.
        nonlocal restart_reads
        joined = " ".join(argv)
        if "current-context" in joined:
            return _result(f"{config.context}\n")
        if "hpa/fraudlens-api" in joined:
            return _result(
                '{"status":{"currentReplicas":3,"desiredReplicas":5,"currentMetrics":['
                '{"type":"Resource","resource":{"name":"cpu","current":'
                '{"averageUtilization":88}}}]}}'
            )
        if "deployment/fraudlens-api" in joined:
            return _result(
                '{"spec":{"template":{"spec":{"containers":[{"name":"api",'
                '"image":"app:sha","resources":{"requests":{"cpu":"100m",'
                '"memory":"512Mi"},"limits":{"cpu":"1","memory":"1Gi"}}}]}}}}'
            )
        if "get pod/worker-b" in joined:
            restart_reads += 1
            return _result(
                '{"status":{"containerStatuses":[{"name":"worker","restartCount":'
                f"{int(restart_reads > 1)}"
                "}]}}"
            )
        if "get pods" in joined:
            return _result(
                '{"items":[{"metadata":{"name":"worker-b"},"status":{"phase":"Running"}},'
                '{"metadata":{"name":"worker-a","deletionTimestamp":"2026-09-14T12:00:00Z"},'
                '"status":{"phase":"Running"}}]}'
            )
        if "logs pod/worker-b" in joined:
            return _result('{"event":"investigation.worker_claimed"}\n')
        return _result()

    runner = FakeRunner(respond)
    kubectl = Kubectl(config, runner=runner, binary="kubectl")
    hpa = kubectl.hpa_observation()
    assert (hpa.current_replicas, hpa.desired_replicas, hpa.cpu_average_utilization) == (3, 5, 88)
    deployment = kubectl.deployment_observation()
    assert deployment.image == "app:sha"
    assert deployment.memory_request == "512Mi"
    kubectl.kill_worker_process("worker-b")
    assert kubectl.wait_for_worker_claim(platform="kind", confirmed=False) == "worker-b"
    assert kubectl.delete_worker_pod("worker-b") == "worker-b"
    assert any(any("fraudlens-worker.pid" in part for part in call) for call in runner.calls)
    assert any("--grace-period=0" in call for call in runner.calls)
    with pytest.raises(CommandError, match="no matching active"):
        kubectl.delete_worker_pod("worker-missing")


def test_worker_kill_requires_an_observed_container_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config().model_copy(update={"worker_claim_timeout_seconds": 1})

    def respond(argv: list[str]) -> CommandResult:
        joined = " ".join(argv)
        if "current-context" in joined:
            return _result(f"{config.context}\n")
        if "get pod/worker-a" in joined:
            return _result('{"status":{"containerStatuses":[{"name":"worker","restartCount":0}]}}')
        return _result()

    moments = iter((1000.0, 1000.0, 1001.0))
    monkeypatch.setattr(kubectl_module.time, "monotonic", lambda: next(moments))
    monkeypatch.setattr(kubectl_module.time, "sleep", lambda _seconds: None)
    kubectl = Kubectl(config, runner=FakeRunner(respond), binary="kubectl")
    with pytest.raises(CommandError, match="did not restart"):
        kubectl.kill_worker_process("worker-a")


def test_kind_create_load_delete_and_clean_verification() -> None:
    config = load_config()
    clusters_calls = 0

    def respond(argv: list[str]) -> CommandResult:
        nonlocal clusters_calls
        joined = " ".join(argv)
        if "get clusters" in joined:
            clusters_calls += 1
            return _result("" if clusters_calls == 1 else f"{config.cluster_name}\n")
        if "current-context" in joined:
            return _result(f"{config.context}\n")
        if "/apis/metrics.k8s.io" in joined:
            return _result('{"items":[{"metadata":{"name":"node"}}]}')
        if "docker ps" in joined:
            return _result("")
        return _result()

    runner = FakeRunner(respond)
    operator = KindOperator(config, runner=runner, kind_binary="kind", docker_binary="docker")
    operator.create()
    operator.load_image("app:proof")
    operator.delete()
    flattened = [" ".join(call) for call in runner.calls]
    assert any("create cluster" in call and config.kind_node_image in call for call in flattened)
    assert any("load docker-image app:proof" in call for call in flattened)
    assert any("delete cluster" in call for call in flattened)


def test_kind_rejects_list_failure_bad_metrics_and_residue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config().model_copy(update={"metrics_timeout_seconds": 60})
    failed = KindOperator(
        config,
        runner=FakeRunner(lambda _argv: _result(returncode=1)),
        kind_binary="kind",
        docker_binary="docker",
    )
    with pytest.raises(CommandError, match="list kind"):
        failed.clusters()

    calls = 0

    def residue(argv: list[str]) -> CommandResult:
        nonlocal calls
        calls += 1
        if "docker" in argv[0]:
            return _result("container-id\n")
        return _result(f"{config.context}\n")

    dirty = KindOperator(
        config,
        runner=FakeRunner(residue),
        kind_binary="kind",
        docker_binary="docker",
    )
    with pytest.raises(CommandError, match="residual"):
        dirty.verify_clean()
    assert calls >= 1

    moments = iter((1000.0, 1061.0))
    monkeypatch.setattr("lib.k8s_demo.kind.time.monotonic", lambda: next(moments))
    timeout_operator = KindOperator(
        config,
        runner=FakeRunner(lambda _argv: _result("not-json")),
        kind_binary="kind",
        docker_binary="docker",
    )
    with pytest.raises(CommandError, match="did not publish"):
        timeout_operator.wait_for_metrics()
