"""Summary: Injectable subprocess and kubectl boundary for the Kubernetes demonstration harness.

Key classes:
- CommandError: sanitized subprocess or safety-gate failure.
- CommandResult: PHI-free command outcome used by fake and real runners.
- CommandRunner: protocol for dependency-injected command execution.
- Kubectl: context-guarded Kubernetes reads, mutations, waits, and typed JSON parsers.
- HpaObservation: one autoscaler status observation.
- DeploymentObservation: selected Deployment facts for evidence.

Key functions:
- run_command: execute a bounded local command without a shell.
- resolve_tool: prefer PATH, then the gitignored .local/tools cache.

Notes:
- All mutating local operations assert the exact kind context. Non-kind mutation additionally
  requires the caller's explicit confirmation; command output never contains Secret data.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from lib.k8s_demo.config import REPO_ROOT, K8sDemoConfig


class CommandError(RuntimeError):
    """Raised with a sanitized command and exit status when a subprocess fails."""


class CommandResult(BaseModel):
    """Captured subprocess result safe for orchestration decisions and tests."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    returncode: int = Field(..., description="Process exit code.")
    stdout: str = Field(default="", description="Captured standard output.")
    stderr: str = Field(default="", description="Captured standard error.")


class CommandRunner(Protocol):
    """Callable command boundary implemented by subprocess and unit-test fakes."""

    def __call__(
        self,
        command: Sequence[str],
        *,
        input_text: str | None = None,
        check: bool = True,
        timeout: float | None = None,
    ) -> CommandResult: ...


class HpaObservation(BaseModel):
    """Autoscaler status fields sampled for hash-bound evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    current_replicas: int = Field(..., ge=0, description="Observed current replicas.")
    desired_replicas: int = Field(..., ge=0, description="Observed desired replicas.")
    cpu_average_utilization: int | None = Field(
        default=None, ge=0, description="Reported average CPU utilization percentage."
    )


class DeploymentObservation(BaseModel):
    """Evidence-bearing API Deployment image and resource contract."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    image: str = Field(..., description="Running API image reference.")
    cpu_request: str = Field(..., description="API CPU request.")
    memory_request: str = Field(..., description="API memory request.")
    cpu_limit: str = Field(..., description="API CPU limit.")
    memory_limit: str = Field(..., description="API memory limit.")


def resolve_tool(name: str) -> str:
    """Resolve a CLI from PATH or the repo-local, gitignored tool cache."""
    discovered = shutil.which(name)
    if discovered:
        return discovered
    cached = REPO_ROOT / ".local" / "tools" / name
    if cached.is_file():
        return str(cached)
    raise CommandError(f"required tool is unavailable: {name}")


def run_command(
    command: Sequence[str],
    *,
    input_text: str | None = None,
    check: bool = True,
    timeout: float | None = None,
) -> CommandResult:
    """Run a command without a shell and return its captured, text-mode result."""
    completed = subprocess.run(
        list(command),
        cwd=REPO_ROOT,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    result = CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    if check and result.returncode != 0:
        safe_command = " ".join(
            Path(part).name if index == 0 else part for index, part in enumerate(command)
        )
        raise CommandError(f"command failed ({result.returncode}): {safe_command}")
    return result


class Kubectl:
    """Small kubectl adapter with mandatory context checks before mutations."""

    def __init__(
        self,
        config: K8sDemoConfig,
        *,
        runner: CommandRunner = run_command,
        binary: str | None = None,
    ) -> None:
        """Bind immutable demo config and an injectable runner."""
        self.config = config
        self.runner = runner
        self.binary = binary or resolve_tool("kubectl")

    def run(
        self,
        args: Sequence[str],
        *,
        input_text: str | None = None,
        check: bool = True,
        timeout: float | None = None,
    ) -> CommandResult:
        """Execute kubectl with the supplied arguments."""
        return self.runner(
            [self.binary, *args], input_text=input_text, check=check, timeout=timeout
        )

    def current_context(self) -> str:
        """Return the current context, or an empty string when kubeconfig has none."""
        result = self.run(["config", "current-context"], check=False)
        return result.stdout.strip() if result.returncode == 0 else ""

    def assert_mutation_allowed(self, *, platform: str, confirmed: bool = False) -> None:
        """Allow exact kind mutations; require confirmation for every non-kind context."""
        context = self.current_context()
        if platform == "kind":
            if context != self.config.context:
                raise CommandError(
                    f"refusing local mutation: context is '{context or '<unset>'}', "
                    f"expected '{self.config.context}'"
                )
            return
        if not confirmed:
            raise CommandError("non-kind Kubernetes mutation requires explicit confirmation")
        if context.startswith("kind-") or not context:
            raise CommandError("AKS mutation requires a non-kind, non-empty context")

    def apply(self, manifest: str, *, platform: str, confirmed: bool = False) -> None:
        """Apply an in-memory manifest after enforcing the platform mutation gate."""
        self.assert_mutation_allowed(platform=platform, confirmed=confirmed)
        self.run(["apply", "-f", "-"], input_text=manifest)

    def wait_for(self, resource: str, condition: str, timeout_seconds: int) -> None:
        """Wait for one namespaced resource condition on the guarded kind context."""
        self.assert_mutation_allowed(platform="kind")
        self.run(
            [
                "-n",
                self.config.namespace,
                "wait",
                resource,
                f"--for=condition={condition}",
                f"--timeout={timeout_seconds}s",
            ],
            timeout=timeout_seconds + 10,
        )

    def get_json(self, resource: str) -> str:
        """Read one namespaced resource as JSON."""
        return self.run(["-n", self.config.namespace, "get", resource, "-o", "json"]).stdout

    def hpa_observation(self) -> HpaObservation:
        """Parse the API HPA status into a typed observation."""
        document = json.loads(self.get_json("hpa/fraudlens-api"))
        status = document.get("status", {})
        metrics = status.get("currentMetrics", [])
        utilization = None
        for metric in metrics:
            if metric.get("type") == "Resource" and metric.get("resource", {}).get("name") == "cpu":
                utilization = metric["resource"].get("current", {}).get("averageUtilization")
        return HpaObservation(
            current_replicas=status.get("currentReplicas", 0),
            desired_replicas=status.get("desiredReplicas", 0),
            cpu_average_utilization=utilization,
        )

    def deployment_observation(self) -> DeploymentObservation:
        """Parse the primary API container's image and resource envelope."""
        document = json.loads(self.get_json("deployment/fraudlens-api"))
        container = next(
            item
            for item in document["spec"]["template"]["spec"]["containers"]
            if item["name"] == "api"
        )
        resources = container["resources"]
        return DeploymentObservation(
            image=container["image"],
            cpu_request=resources["requests"]["cpu"],
            memory_request=resources["requests"]["memory"],
            cpu_limit=resources["limits"]["cpu"],
            memory_limit=resources["limits"]["memory"],
        )

    def delete_worker_pod(self) -> str:
        """Delete one worker pod for the local durability proof and return its safe name."""
        self.assert_mutation_allowed(platform="kind")
        pods = json.loads(
            self.run(
                [
                    "-n",
                    self.config.namespace,
                    "get",
                    "pods",
                    "-l",
                    "app.kubernetes.io/component=worker",
                    "-o",
                    "json",
                ]
            ).stdout
        )
        names = sorted(item["metadata"]["name"] for item in pods.get("items", []))
        if not names:
            raise CommandError("durability proof found no worker pod to delete")
        self.run(
            [
                "-n",
                self.config.namespace,
                "delete",
                "pod",
                names[0],
                "--grace-period=0",
                "--force",
                "--wait=false",
            ]
        )
        return str(names[0])
