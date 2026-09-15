"""Behavioral tests for the deterministic kind worker-crash database barrier."""

from __future__ import annotations

import subprocess

import pytest

from lib.k8s_demo import fault
from lib.k8s_demo.config import load_config
from lib.k8s_demo.kubectl import CommandError, CommandResult


class FakeProcess:
    """Small Popen substitute with controllable lifecycle outcomes."""

    def __init__(self, *, exited: bool = False, wait_timeout: bool = False) -> None:
        self.exited = exited
        self.wait_timeout = wait_timeout
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return 1 if self.exited else None

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, *, timeout: int) -> int:
        assert timeout == 5
        if self.wait_timeout and not self.killed:
            raise subprocess.TimeoutExpired("kubectl", timeout)
        self.exited = True
        return 0


class FakeKubectl:
    """Record barrier queries while serving a configurable lock count."""

    binary = "kubectl"

    def __init__(self, lock_count: str = "1") -> None:
        self.lock_count = lock_count
        self.calls: list[list[str]] = []

    def assert_mutation_allowed(self, *, platform: str) -> None:
        assert platform == "kind"

    def run(self, args: list[str], *, check: bool = True) -> CommandResult:
        del check
        self.calls.append(args)
        output = self.lock_count if "pg_locks" in args[-1] else ""
        return CommandResult(returncode=0, stdout=output, stderr="")


def test_barrier_acquires_and_releases_named_database_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config()
    kubectl = FakeKubectl()
    process = FakeProcess(wait_timeout=True)
    popen_commands: list[list[str]] = []

    def popen(command: list[str], **_kwargs: object) -> FakeProcess:
        popen_commands.append(command)
        return process

    monkeypatch.setattr(fault.subprocess, "Popen", popen)
    with fault.hold_transaction_reads(config, kubectl):
        assert popen_commands[0][0:3] == ["kubectl", "--context", config.context]
    assert process.terminated is True
    assert process.killed is True
    assert any("pg_terminate_backend" in call[-1] for call in kubectl.calls)


def test_barrier_rejects_early_exit_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_config().model_copy(
        update={"worker_claim_timeout_seconds": 5, "worker_claim_poll_seconds": 0.05}
    )
    kubectl = FakeKubectl(lock_count="0")
    exited = FakeProcess(exited=True)
    with pytest.raises(CommandError, match="exited"):
        fault._wait_for_lock(config, kubectl, exited)

    waiting = FakeProcess()
    moments = iter((0.0, 0.0, 5.0))
    monkeypatch.setattr(fault.time, "monotonic", lambda: next(moments))
    monkeypatch.setattr(fault.time, "sleep", lambda _seconds: None)
    with pytest.raises(CommandError, match="did not acquire"):
        fault._wait_for_lock(config, kubectl, waiting)


def test_release_is_noop_for_finished_process() -> None:
    config = load_config()
    kubectl = FakeKubectl()
    process = FakeProcess(exited=True)
    fault._release_lock(config, kubectl, process)
    assert process.terminated is False
