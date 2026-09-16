"""Summary: Operational-probe smoke execution for the kind and approved AKS Kubernetes targets.

Key classes:
- (none)

Key functions:
- run_smoke: reach the API through the platform's own entry point and run the remote smoke suite.

Notes:
- kind has no external address, so it is reached through a port-forward that is always torn down.
  AKS publishes the overlay's LoadBalancer Service, so the session probes that address directly and
  proves the same path a browser would take. Neither route carries credentials or PHI.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext, suppress
from urllib.error import URLError
from urllib.request import urlopen

from lib.k8s_demo.config import REPO_ROOT, K8sDemoConfig
from lib.k8s_demo.kubectl import CommandError, Kubectl

_HTTP_OK = 200
_PROBE_DEADLINE_SECONDS = 60
_PROBE_TIMEOUT_SECONDS = 2
_PROBE_RETRY_SECONDS = 1
_PROBES = ("healthz", "readyz")


@contextmanager
def _port_forward(config: K8sDemoConfig, kubectl: Kubectl) -> Iterator[str]:
    """Forward the guarded Service to a loopback port and always stop the child process."""
    process = subprocess.Popen(
        [
            kubectl.binary,
            "-n",
            config.namespace,
            "port-forward",
            f"service/{config.service}",
            f"{config.local_port}:8000",
        ],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        yield f"{str(config.smoke_base_url).rstrip('/')}:{config.local_port}"
    finally:
        process.terminate()
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)
        if process.poll() is None:
            process.kill()


def _external_base_url(config: K8sDemoConfig, kubectl: Kubectl) -> str:
    """Resolve the LoadBalancer address the AKS overlay publishes for the API Service."""
    address = kubectl.load_balancer_address(config.service)
    return f"{config.aks.external_scheme}://{address}:{config.aks.external_port}"


def _await_probes(base_url: str, platform: str) -> None:
    """Require both unprefixed operational probes to answer 200 before the suite runs."""
    deadline = time.monotonic() + _PROBE_DEADLINE_SECONDS
    pending = set(_PROBES)
    while pending and time.monotonic() < deadline:
        for endpoint in tuple(pending):
            try:
                with urlopen(f"{base_url}/{endpoint}", timeout=_PROBE_TIMEOUT_SECONDS) as response:
                    if response.status == _HTTP_OK:
                        pending.remove(endpoint)
            except (OSError, URLError):
                pass
        if pending:
            time.sleep(_PROBE_RETRY_SECONDS)
    if pending:
        raise CommandError(f"{platform} smoke probes did not become ready")


def _run_remote_tests(base_url: str) -> None:
    """Run the smoke-marked suite against the resolved base URL."""
    environment = os.environ.copy()
    environment["SMOKE_BASE_URL"] = base_url
    completed = subprocess.run(
        ["uv", "run", "pytest", "tests/smoke", "-m", "smoke", "--no-cov", "-q"],
        cwd=REPO_ROOT,
        env=environment,
        check=False,
    )
    if completed.returncode != 0:
        raise CommandError("remote smoke tests failed")


def run_smoke(config: K8sDemoConfig, *, platform: str = "kind", confirmed: bool = False) -> None:
    """Probe the platform's own API entry point and require the remote smoke suite to pass."""
    kubectl = Kubectl(config)
    kubectl.assert_mutation_allowed(platform=platform, confirmed=confirmed)
    entry = (
        nullcontext(_external_base_url(config, kubectl))
        if platform == "aks"
        else _port_forward(config, kubectl)
    )
    with entry as base_url:
        _await_probes(base_url, platform)
        _run_remote_tests(base_url)
