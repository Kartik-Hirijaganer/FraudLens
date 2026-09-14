"""Behavioral tests for the Kubernetes CLI dispatch and live orchestration state machines."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

import k8s_demo
from lib.k8s_demo.config import load_config
from lib.k8s_demo.evidence import ScalingSample
from lib.k8s_demo.kubectl import CommandError, CommandResult, HpaObservation
from lib.k8s_demo.load import SUMMARY_PREFIX, LoadSummary
from lib.k8s_demo.render import RenderedManifest


def _summary(*, mode: str = "healthz", runs: int = 0) -> LoadSummary:
    return LoadSummary(
        mode=mode,
        requests=max(1, runs),
        succeeded=max(1, runs),
        failed=0,
        duration_seconds=1,
        latency_p50_ms=1,
        latency_p95_ms=2,
        runs_submitted=runs,
        runs_completed=runs,
        runs_failed=0,
        max_run_attempts=2 if runs else 0,
    )


class FakeKubectl:
    """Minimal mutable fake covering scaling and durability orchestration."""

    def __init__(self, samples: Iterator[ScalingSample] | None = None) -> None:
        self.config = load_config()
        self.samples = samples
        self.calls: list[tuple[str, ...]] = []
        self.applied: list[str] = []

    def run(
        self,
        args: list[str],
        *,
        input_text: str | None = None,
        check: bool = True,
        timeout: float | None = None,
    ) -> CommandResult:
        del input_text, check, timeout
        self.calls.append(tuple(args))
        stdout = "worker-old-123\n" if "psql" in args else ""
        return CommandResult(returncode=0, stdout=stdout, stderr="")

    def get_json(self, resource: str) -> str:
        if resource.startswith("hpa/"):
            return json.dumps({"spec": {"minReplicas": 1, "maxReplicas": 5}})
        return '{"status":{"succeeded":1}}'

    def hpa_observation(self) -> HpaObservation:
        assert self.samples is not None
        sample = next(self.samples)
        return HpaObservation(
            current_replicas=sample.replicas,
            desired_replicas=sample.desired_replicas,
            cpu_average_utilization=sample.cpu_percent,
        )

    def assert_mutation_allowed(self, *, platform: str, confirmed: bool = False) -> None:
        assert platform == "kind" or (platform == "aks" and confirmed)

    def apply(self, manifest: str, *, platform: str, confirmed: bool = False) -> None:
        assert platform == "kind" or (platform == "aks" and confirmed)
        self.applied.append(manifest)

    def wait_for(
        self,
        resource: str,
        condition: str,
        timeout_seconds: int,
        *,
        platform: str = "kind",
        confirmed: bool = False,
    ) -> None:
        assert platform == "kind" or (platform == "aks" and confirmed)
        assert condition in {"Available", "Complete"}
        assert resource
        assert timeout_seconds > 0

    def delete_worker_pod(
        self,
        pod_name: str | None = None,
        *,
        platform: str = "kind",
        confirmed: bool = False,
    ) -> str:
        assert platform == "kind" or (platform == "aks" and confirmed)
        assert pod_name == "worker-old"
        return "worker-old"

    def kill_worker_process(
        self,
        pod_name: str,
        *,
        platform: str = "kind",
        confirmed: bool = False,
    ) -> None:
        assert platform == "kind" or (platform == "aks" and confirmed)
        assert pod_name == "worker-old"
        self.calls.append(("kill", pod_name))

    def wait_for_worker_claim(self, *, platform: str, confirmed: bool) -> str:
        assert platform == "aks" and confirmed is True
        return "worker-old"


def test_main_returns_safe_statuses(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(k8s_demo, "_dispatch", lambda _args, _config: None)
    assert k8s_demo.main(["tools-check"]) == 0

    def fail(_args: object, _config: object) -> None:
        raise CommandError("safe reason")

    monkeypatch.setattr(k8s_demo, "_dispatch", fail)
    assert k8s_demo.main(["tools-check"]) == 1
    assert "safe reason" in capsys.readouterr().err


def test_dispatch_routes_lifecycle_render_deploy_and_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = load_config()
    called: list[str] = []

    class FakeKind:
        def __init__(self, _config: object) -> None:
            pass

        def create(self) -> None:
            called.append("up")

        def delete(self) -> None:
            called.append("down")

        def load_image(self) -> None:
            called.append("load")

    monkeypatch.setattr(k8s_demo, "KindOperator", FakeKind)
    monkeypatch.setattr(k8s_demo, "_tools_check", lambda _config: called.append("tools"))
    monkeypatch.setattr(k8s_demo, "verify_kind_clean", lambda _config: called.append("clean"))
    monkeypatch.setattr(k8s_demo, "_smoke", lambda _config, **_kwargs: called.append("smoke"))
    monkeypatch.setattr(
        k8s_demo,
        "render_overlay",
        lambda *_args, **_kwargs: RenderedManifest(
            platform="kind", image="app:sha", yaml_text="kind: Namespace\n"
        ),
    )
    monkeypatch.setattr(k8s_demo, "deploy", lambda *_args, **_kwargs: called.append("deploy"))
    monkeypatch.setattr(k8s_demo, "sync_secrets", lambda *_args, **_kwargs: ["one"])
    parser = k8s_demo.build_parser()
    for command in ("tools-check", "kind-up", "kind-down", "kind-load", "smoke", "verify-clean"):
        k8s_demo._dispatch(parser.parse_args([command]), config)
    k8s_demo._dispatch(parser.parse_args(["render", "--platform", "kind"]), config)
    assert "kind: Namespace" in capsys.readouterr().out
    output = tmp_path / "rendered.yaml"
    k8s_demo._dispatch(
        parser.parse_args(["render", "--platform", "kind", "--output", str(output)]),
        config,
    )
    assert output.read_text(encoding="utf-8") == "kind: Namespace\n"
    k8s_demo._dispatch(parser.parse_args(["deploy", "--platform", "kind"]), config)
    k8s_demo._dispatch(parser.parse_args(["secrets-sync", "--confirm-aks"]), config)
    assert called == ["tools", "up", "down", "load", "smoke", "clean", "deploy"]
    assert "values redacted" in capsys.readouterr().out


def test_dispatch_load_and_evidence_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = load_config()
    report = SimpleNamespace()
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(k8s_demo, "load_config_from_env", lambda: config.load)
    monkeypatch.setattr(k8s_demo, "run_load", lambda _config: _summary())
    monkeypatch.setattr(k8s_demo, "_hpa_demo", lambda _config, **_kwargs: report)
    monkeypatch.setattr(k8s_demo, "load_evidence", lambda _content: report)
    monkeypatch.setattr(k8s_demo, "validate_evidence", lambda _report: None)
    monkeypatch.setattr(k8s_demo, "render_markdown", lambda _report: "rendered evidence")
    parser = k8s_demo.build_parser()
    k8s_demo._dispatch(parser.parse_args(["load-in-cluster"]), config)
    k8s_demo._dispatch(parser.parse_args(["hpa-demo"]), config)
    k8s_demo._dispatch(parser.parse_args(["evidence-validate", "--path", str(evidence)]), config)
    k8s_demo._dispatch(parser.parse_args(["evidence-render", "--path", str(evidence)]), config)
    output = capsys.readouterr().out
    assert "K8S_DEMO_SUMMARY=" in output
    assert output.count("rendered evidence") == 2


def test_job_helpers_cover_success_failure_logs_apply_and_samples() -> None:
    kubectl = FakeKubectl(
        iter([ScalingSample(elapsed_seconds=0, replicas=2, desired_replicas=3, cpu_percent=80)])
    )
    assert k8s_demo._job_complete(kubectl) is True
    kubectl.get_json = lambda _resource: '{"status":{"failed":1}}'  # type: ignore[method-assign]
    with pytest.raises(CommandError, match="load Job failed"):
        k8s_demo._job_complete(kubectl)
    kubectl.run = lambda *_args, **_kwargs: CommandResult(  # type: ignore[method-assign]
        returncode=1, stdout="", stderr=""
    )
    assert k8s_demo._job_logs(kubectl, check=False) == ""
    k8s_demo._start_load_job(kubectl, "manifest")
    assert kubectl.applied == ["manifest"]
    sample = k8s_demo._sample(kubectl, k8s_demo.time.monotonic())
    assert sample.replicas == 2


def test_scaling_state_machine_reaches_max_and_returns_to_min(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config().model_copy(update={"sample_interval_seconds": 5})
    samples = iter(
        [
            ScalingSample(elapsed_seconds=0, replicas=1, desired_replicas=1, cpu_percent=10),
            ScalingSample(elapsed_seconds=5, replicas=5, desired_replicas=5, cpu_percent=90),
            ScalingSample(elapsed_seconds=10, replicas=1, desired_replicas=1, cpu_percent=5),
        ]
    )
    kubectl = FakeKubectl(samples)
    monkeypatch.setattr(k8s_demo, "render_load_job", lambda *_args, **_kwargs: "manifest")
    monkeypatch.setattr(k8s_demo, "_start_load_job", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(k8s_demo, "_job_complete", lambda _kubectl: True)
    logs = SUMMARY_PREFIX + _summary().model_dump_json()
    monkeypatch.setattr(k8s_demo, "_job_logs", lambda _kubectl: logs)
    monkeypatch.setattr(k8s_demo.time, "sleep", lambda _seconds: None)
    moments = iter((100.0, 100.0, 105.0, 105.0, 105.0, 110.0))
    monkeypatch.setattr(k8s_demo.time, "monotonic", lambda: next(moments))
    observed, summary, finished = k8s_demo._run_scaling(config, kubectl)
    assert [item.replicas for item in observed] == [1, 5, 1]
    assert summary.failed == 0
    assert finished == 5


def test_scaling_requires_minimum_start(monkeypatch: pytest.MonkeyPatch) -> None:
    samples = iter(
        [ScalingSample(elapsed_seconds=0, replicas=2, desired_replicas=2, cpu_percent=1)]
    )
    kubectl = FakeKubectl(samples)
    monkeypatch.setattr(k8s_demo, "render_load_job", lambda *_args, **_kwargs: "manifest")
    with pytest.raises(CommandError, match="begin at"):
        k8s_demo._run_scaling(load_config(), kubectl)


def test_durability_state_machine_forces_worker_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config()
    kubectl = FakeKubectl()
    final = _summary(mode="investigations", runs=config.load.cases)
    logs = iter(
        [
            f"K8S_DEMO_SUBMITTED={config.load.cases}\n",
            f"{SUMMARY_PREFIX}{final.model_dump_json()}\n",
        ]
    )
    monkeypatch.setattr(k8s_demo, "render_load_job", lambda *_args, **_kwargs: "manifest")
    monkeypatch.setattr(k8s_demo, "_start_load_job", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(k8s_demo, "hold_transaction_reads", lambda *_args: nullcontext())
    monkeypatch.setattr(k8s_demo, "_job_logs", lambda *_args, **_kwargs: next(logs))
    monkeypatch.setattr(k8s_demo.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(k8s_demo.time, "monotonic", lambda: 100.0)
    evidence = k8s_demo._run_durability(config, kubectl)
    assert evidence.runs_completed == config.load.cases
    assert evidence.max_run_attempts == 2
    assert any("--replicas=0" in call for call in kubectl.calls)
    assert any("--replicas=1" in call for call in kubectl.calls)
    assert ("kill", "worker-old") in kubectl.calls


def test_aks_durability_uses_claim_marker_without_local_database_barrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config()
    kubectl = FakeKubectl()
    kubectl.assert_mutation_allowed = lambda **_kwargs: None  # type: ignore[method-assign]
    final = _summary(mode="investigations", runs=config.load.cases)
    logs = iter(
        [
            f"K8S_DEMO_SUBMITTED={config.load.cases}\n",
            f"{SUMMARY_PREFIX}{final.model_dump_json()}\n",
        ]
    )
    monkeypatch.setattr(k8s_demo, "render_load_job", lambda *_args, **_kwargs: "manifest")
    monkeypatch.setattr(k8s_demo, "_start_load_job", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        k8s_demo,
        "hold_transaction_reads",
        lambda *_args: pytest.fail("AKS must not use the local PostgreSQL barrier"),
    )
    monkeypatch.setattr(k8s_demo, "_job_logs", lambda *_args, **_kwargs: next(logs))
    monkeypatch.setattr(k8s_demo.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(k8s_demo.time, "monotonic", lambda: 100.0)
    evidence = k8s_demo._run_durability(config, kubectl, platform="aks", confirmed=True)
    assert evidence.runs_completed == config.load.cases


def test_active_claim_wait_times_out_when_no_lease_is_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config().model_copy(
        update={"worker_claim_timeout_seconds": 5, "worker_claim_poll_seconds": 0.05}
    )
    kubectl = FakeKubectl()
    kubectl.run = lambda *_args, **_kwargs: CommandResult(  # type: ignore[method-assign]
        returncode=0, stdout="not-a-count", stderr=""
    )
    moments = iter((0.0, 0.0, 5.0))
    monkeypatch.setattr(k8s_demo.time, "monotonic", lambda: next(moments))
    monkeypatch.setattr(k8s_demo.time, "sleep", lambda _seconds: None)
    with pytest.raises(CommandError, match="running lease"):
        k8s_demo._wait_for_active_claim(config, kubectl)


def test_tools_check_accepts_pins_and_rejects_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_config()
    monkeypatch.setattr(k8s_demo, "resolve_tool", lambda name: name)

    def run(command: list[str]) -> CommandResult:
        if command[0] == "kubectl":
            output = '{"clientVersion":{"gitVersion":"v' + config.kubectl_version + '"}}'
        elif command[0] == "kind":
            output = f"kind v{config.kind_version}"
        else:
            output = config.kubeconform_version
        return CommandResult(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(k8s_demo, "run_command", run)
    k8s_demo._tools_check(config)

    monkeypatch.setattr(
        k8s_demo,
        "run_command",
        lambda command: CommandResult(
            returncode=0,
            stdout="not-json" if command[0] == "kubectl" else "unused",
            stderr="",
        ),
    )
    with pytest.raises(CommandError, match="kubectl CLI version output"):
        k8s_demo._tools_check(config)

    monkeypatch.setattr(
        k8s_demo,
        "run_command",
        lambda command: CommandResult(
            returncode=0,
            stdout=(
                '{"clientVersion":{"gitVersion":"v0.0.0"}}' if command[0] == "kubectl" else "unused"
            ),
            stderr="",
        ),
    )
    with pytest.raises(CommandError, match="kubectl CLI version does not match"):
        k8s_demo._tools_check(config)

    monkeypatch.setattr(
        k8s_demo,
        "run_command",
        lambda command: CommandResult(
            returncode=0,
            stdout=(
                '{"clientVersion":{"gitVersion":"v' + config.kubectl_version + '"}}'
                if command[0] == "kubectl"
                else "wrong"
            ),
            stderr="",
        ),
    )
    with pytest.raises(CommandError, match="kind CLI"):
        k8s_demo._tools_check(config)


def test_smoke_runs_remote_tests_and_always_stops_forward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config()
    terminated: list[bool] = []

    class FakeProcess:
        def terminate(self) -> None:
            terminated.append(True)

        def wait(self, timeout: int) -> int:
            assert timeout == 5
            return 0

        def poll(self) -> int:
            return 0

        def kill(self) -> None:
            raise AssertionError("kill should not be needed")

    class Response(AbstractContextManager["Response"]):
        status = 200

        def __exit__(self, *args: object) -> None:
            del args

    fake_kubectl = SimpleNamespace(
        binary="kubectl",
        assert_mutation_allowed=lambda **_kwargs: None,
    )
    monkeypatch.setattr(k8s_demo, "Kubectl", lambda _config: fake_kubectl)
    monkeypatch.setattr(k8s_demo.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr(k8s_demo, "urlopen", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(
        k8s_demo.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], returncode=0),
    )
    k8s_demo._smoke(config)
    assert terminated == [True]
