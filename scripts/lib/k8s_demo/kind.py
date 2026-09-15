"""Summary: Guarded kind lifecycle, image loading, metrics readiness, and absence verification.

Key classes:
- KindOperator: operates only the configured local kind cluster through injectable commands.

Key functions:
- (none)

Notes:
- kind is the sole mutation path here. No method authenticates to or mutates a cloud provider.
"""

from __future__ import annotations

import json
import time

from lib.k8s_demo.config import REPO_ROOT, K8sDemoConfig
from lib.k8s_demo.kubectl import (
    CommandError,
    CommandRunner,
    Kubectl,
    resolve_tool,
    run_command,
)


class KindOperator:
    """Create, populate, observe, and delete exactly one configured local kind cluster."""

    def __init__(
        self,
        config: K8sDemoConfig,
        *,
        runner: CommandRunner = run_command,
        kind_binary: str | None = None,
        docker_binary: str | None = None,
    ) -> None:
        """Bind tool paths and immutable cluster policy."""
        self.config = config
        self.runner = runner
        self.kind_binary = kind_binary or resolve_tool("kind")
        self.docker_binary = docker_binary or resolve_tool("docker")
        self.kubectl = Kubectl(config, runner=runner)

    def clusters(self) -> list[str]:
        """Return local kind cluster names."""
        result = self.runner([self.kind_binary, "get", "clusters"], check=False)
        if result.returncode != 0:
            raise CommandError("unable to list kind clusters")
        return [line for line in result.stdout.splitlines() if line]

    def create(self) -> None:
        """Create the configured pinned cluster and install the kind-only metrics server."""
        if self.config.cluster_name not in self.clusters():
            cluster_config = REPO_ROOT / "deploy" / "kind" / "cluster.yaml"
            self.runner(
                [
                    self.kind_binary,
                    "create",
                    "cluster",
                    "--name",
                    self.config.cluster_name,
                    "--image",
                    self.config.kind_node_image,
                    "--config",
                    str(cluster_config),
                    "--wait",
                    f"{self.config.startup_timeout_seconds}s",
                ],
                timeout=self.config.startup_timeout_seconds + 60,
            )
        self.kubectl.assert_mutation_allowed(platform="kind")
        addon = REPO_ROOT / "deploy" / "k8s" / "addons" / "metrics-server-kind" / "components.yaml"
        self.kubectl.run(["apply", "-f", str(addon)])
        self.kubectl.run(
            [
                "-n",
                "kube-system",
                "rollout",
                "status",
                "deployment/metrics-server",
                f"--timeout={self.config.metrics_timeout_seconds}s",
            ],
            timeout=self.config.metrics_timeout_seconds + 10,
        )
        self.wait_for_metrics()

    def wait_for_metrics(self) -> None:
        """Wait until the resource metrics API returns at least one node."""
        deadline = time.monotonic() + self.config.metrics_timeout_seconds
        while time.monotonic() < deadline:
            result = self.kubectl.run(
                ["get", "--raw", "/apis/metrics.k8s.io/v1beta1/nodes"], check=False
            )
            if result.returncode == 0:
                try:
                    if json.loads(result.stdout).get("items"):
                        return
                except json.JSONDecodeError:
                    pass
            time.sleep(2)
        raise CommandError("metrics-server did not publish node metrics before timeout")

    def load_image(self, image: str | None = None) -> None:
        """Load the already-built local backend image into the configured kind nodes."""
        self.kubectl.assert_mutation_allowed(platform="kind")
        self.runner(
            [
                self.kind_binary,
                "load",
                "docker-image",
                image or self.config.image,
                "--name",
                self.config.cluster_name,
            ]
        )

    def delete(self) -> None:
        """Delete only the configured cluster, then prove no matching Docker nodes remain."""
        if self.config.cluster_name in self.clusters():
            self.runner([self.kind_binary, "delete", "cluster", "--name", self.config.cluster_name])
        self.verify_clean()

    def verify_clean(self) -> None:
        """Fail if any kind node for this demo remains in Docker."""
        result = self.runner(
            [
                self.docker_binary,
                "ps",
                "-a",
                "--filter",
                f"label=io.x-k8s.kind.cluster={self.config.cluster_name}",
                "--format",
                "{{.ID}}",
            ]
        )
        if result.stdout.strip():
            raise CommandError("kind cleanup verification found residual cluster containers")
