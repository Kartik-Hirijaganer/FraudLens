"""Behavioral tests for platform-aware smoke execution against kind and approved AKS."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from contextlib import AbstractContextManager
from types import SimpleNamespace

import pytest

from lib.k8s_demo import smoke
from lib.k8s_demo.config import load_config
from lib.k8s_demo.kubectl import CommandError, CommandResult, Kubectl


class _Response(AbstractContextManager["_Response"]):
    """Minimal urlopen stand-in whose probe always answers 200."""

    status = 200

    def __exit__(self, *args: object) -> None:
        del args


class _FakeProcess:
    """Port-forward child process that records its own termination."""

    def __init__(self, terminated: list[bool]) -> None:
        self._terminated = terminated

    def terminate(self) -> None:
        self._terminated.append(True)

    def wait(self, timeout: int) -> int:
        assert timeout == 5
        return 0

    def poll(self) -> int:
        return 0

    def kill(self) -> None:
        raise AssertionError("kill should not be needed")


def _passing_pytest(monkeypatch: pytest.MonkeyPatch, urls: list[str]) -> None:
    """Stub the probe fetch and the remote suite, capturing the base URL each reaches."""
    monkeypatch.setattr(smoke, "urlopen", lambda url, **_kwargs: urls.append(url) or _Response())

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command[:4] == ["uv", "run", "pytest", "tests/smoke"]
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        urls.append(str(environment["SMOKE_BASE_URL"]))
        return subprocess.CompletedProcess([], returncode=0)

    monkeypatch.setattr(
        smoke.subprocess,
        "run",
        run,
    )


def test_kind_smoke_runs_remote_tests_and_always_stops_the_forward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config()
    terminated: list[bool] = []
    urls: list[str] = []
    monkeypatch.setattr(
        smoke, "Kubectl", lambda _config: SimpleNamespace(binary="kubectl", **_allowed())
    )
    monkeypatch.setattr(
        smoke.subprocess, "Popen", lambda *_args, **_kwargs: _FakeProcess(terminated)
    )
    _passing_pytest(monkeypatch, urls)

    smoke.run_smoke(config)

    assert terminated == [True]
    assert urls[-1] == f"http://127.0.0.1:{config.local_port}"


def test_aks_smoke_targets_the_load_balancer_address_without_a_forward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config()
    urls: list[str] = []

    def runner(command: list[str], **_kwargs: object) -> CommandResult:
        if "current-context" in command:
            return CommandResult(returncode=0, stdout="fraudlens-aks-demo-aks\n", stderr="")
        return CommandResult(
            returncode=0,
            stdout='{"status": {"loadBalancer": {"ingress": [{"ip": "203.0.113.10"}]}}}',
            stderr="",
        )

    monkeypatch.setattr(smoke, "Kubectl", lambda cfg: Kubectl(cfg, runner=runner, binary="kubectl"))
    monkeypatch.setattr(
        smoke.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("AKS smoke must not port-forward"),
    )
    _passing_pytest(monkeypatch, urls)

    smoke.run_smoke(config, platform="aks", confirmed=True)

    expected = f"http://203.0.113.10:{config.aks.external_port}"
    assert urls[-1] == expected
    assert all(url.startswith(expected) for url in urls)


def test_aks_smoke_fails_when_no_external_address_is_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config()
    slept: list[float] = []

    def runner(command: list[str], **_kwargs: object) -> CommandResult:
        if "current-context" in command:
            return CommandResult(returncode=0, stdout="fraudlens-aks-demo-aks\n", stderr="")
        return CommandResult(returncode=0, stdout='{"status": {"loadBalancer": {}}}', stderr="")

    kubectl = Kubectl(config, runner=runner, binary="kubectl")
    monkeypatch.setattr(smoke, "Kubectl", lambda _config: kubectl)
    monkeypatch.setattr("lib.k8s_demo.kubectl.time.sleep", slept.append)
    monkeypatch.setattr(
        "lib.k8s_demo.kubectl.time.monotonic",
        _advancing_clock(config.aks.address_timeout_seconds),
    )

    with pytest.raises(CommandError, match="no external address"):
        smoke.run_smoke(config, platform="aks", confirmed=True)
    assert slept == [config.aks.address_poll_seconds]


def test_smoke_reports_probes_that_never_become_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_config()
    monkeypatch.setattr(
        smoke, "Kubectl", lambda _config: SimpleNamespace(binary="kubectl", **_allowed())
    )
    monkeypatch.setattr(smoke.subprocess, "Popen", lambda *_args, **_kwargs: _FakeProcess([]))
    monkeypatch.setattr(
        smoke, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("refused"))
    )
    monkeypatch.setattr(smoke.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(smoke.time, "monotonic", _advancing_clock(smoke._PROBE_DEADLINE_SECONDS))

    with pytest.raises(CommandError, match="kind smoke probes did not become ready"):
        smoke.run_smoke(config)


def test_smoke_reports_a_failing_remote_suite(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_config()
    monkeypatch.setattr(
        smoke, "Kubectl", lambda _config: SimpleNamespace(binary="kubectl", **_allowed())
    )
    monkeypatch.setattr(smoke.subprocess, "Popen", lambda *_args, **_kwargs: _FakeProcess([]))
    monkeypatch.setattr(smoke, "urlopen", lambda *_args, **_kwargs: _Response())
    monkeypatch.setattr(
        smoke.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], returncode=1),
    )

    with pytest.raises(CommandError, match="remote smoke tests failed"):
        smoke.run_smoke(config)


def _allowed() -> dict[str, object]:
    """Return a Kubectl stand-in whose mutation gate passes."""
    return {"assert_mutation_allowed": lambda **_kwargs: None}


def _advancing_clock(deadline: float) -> Callable[[], float]:
    """Return a monotonic stub that starts at zero and then jumps past the deadline."""
    ticks = iter([0.0, 0.0, deadline + 1, deadline + 1, deadline + 1])

    def _clock() -> float:
        return next(ticks, deadline + 1)

    return _clock
