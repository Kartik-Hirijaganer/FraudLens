"""Summary: Local process discovery, cleanup, compose commands, and readiness polling.

Key classes:
- (none)

Key functions:
- compose_command: build the local docker-compose command.
- require_tools:
- compose_down:
- remove_path:
- clear_local_caches: remove generated caches while preserving downloaded datasets.
- listening_pids:
- process_command:
- process_cwd:
- is_under_repo:
- is_fraudlens_listener:
- wait_for_ports_to_drain:
- free_fraudlens_ports: terminate only repository-owned listeners.
- http_ok:
- wait_for_http: poll a bounded health endpoint.
- await_backend_ready:

Notes:
- Listener ownership is verified before any process is signaled.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.local.yml"
LOCAL_STATE_DIR = REPO_ROOT / ".local"
FRONTEND_DIR = REPO_ROOT / "frontend"
HEALTH_TIMEOUT_SECONDS = 60.0
HEALTH_POLL_SECONDS = 1.0
HTTP_OK = 200
PORT_DRAIN_TIMEOUT_SECONDS = 5.0
LOCAL_CACHE_PATHS = (
    LOCAL_STATE_DIR / "artifacts",
    LOCAL_STATE_DIR / "chroma",
    REPO_ROOT / ".pytest_cache",
    REPO_ROOT / ".ruff_cache",
    REPO_ROOT / ".mypy_cache",
    FRONTEND_DIR / "node_modules" / ".vite",
    FRONTEND_DIR / "coverage",
    FRONTEND_DIR / "dist",
    REPO_ROOT / "coverage.xml",
)
REPO_PROCESS_MARKERS = (
    "fraudlens_backend.main:app",
    "scripts/local_demo.py",
    "npm --prefix frontend run dev",
)


def compose_command(*args: str) -> list[str]:
    """Build a `docker compose -f <file> ...` command for the local stack."""
    return ["docker", "compose", "-f", str(COMPOSE_FILE), *args]


def require_tools(*tools: str) -> None:
    """Raise a clear error if any required CLI tool is not on PATH."""
    missing = [tool for tool in tools if shutil.which(tool) is None]
    if missing:
        raise RuntimeError(f"missing required tools: {', '.join(missing)}")


def compose_down(*, remove_volumes: bool) -> None:
    """Stop the FraudLens compose stack, optionally dropping volumes too."""
    args = ["down", "--remove-orphans"]
    if remove_volumes:
        args.append("-v")
    subprocess.run(compose_command(*args), cwd=REPO_ROOT, check=True)


def remove_path(path: Path) -> None:
    """Remove a generated local path when present (directory or file)."""
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def clear_local_caches() -> None:
    """Delete generated demo/check caches while preserving downloaded IBM AML source data."""
    for path in LOCAL_CACHE_PATHS:
        remove_path(path)


def listening_pids(port: str) -> list[int]:
    """Return PIDs listening on a TCP port, or an empty list when `lsof` is unavailable."""
    if shutil.which("lsof") is None:
        return []
    proc = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return sorted({int(line) for line in proc.stdout.splitlines() if line.strip().isdigit()})


def process_command(pid: int) -> str:
    """Return a process command line, best-effort."""
    proc = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def process_cwd(pid: int) -> Path | None:
    """Return a process working directory via lsof, best-effort."""
    if shutil.which("lsof") is None:
        return None
    proc = subprocess.run(
        ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    for line in proc.stdout.splitlines():
        if line.startswith("n"):
            return Path(line[1:])
    return None


def is_under_repo(path: Path | None) -> bool:
    """Return True when path resolves under the FraudLens repository."""
    if path is None:
        return False
    with contextlib.suppress(OSError, RuntimeError):
        return path.resolve().is_relative_to(REPO_ROOT.resolve())
    return False


def is_fraudlens_listener(pid: int) -> bool:
    """Return True when a listener is a FraudLens-owned local dev process."""
    if pid == os.getpid():
        return False
    command = process_command(pid)
    if str(REPO_ROOT) in command or any(marker in command for marker in REPO_PROCESS_MARKERS):
        return True
    return is_under_repo(process_cwd(pid))


def wait_for_ports_to_drain(ports: tuple[str, ...]) -> bool:
    """Wait briefly for all configured ports to have no remaining listeners."""
    deadline = time.monotonic() + PORT_DRAIN_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if not any(listening_pids(port) for port in ports):
            return True
        time.sleep(HEALTH_POLL_SECONDS)
    return not any(listening_pids(port) for port in ports)


def free_fraudlens_ports(ports: tuple[str, ...], *, fail_on_blockers: bool = True) -> list[str]:
    """Terminate FraudLens-owned listeners and optionally report unrelated port owners."""
    blockers: list[str] = []
    to_terminate: set[int] = set()
    ports_to_drain: set[str] = set()
    for port in ports:
        for pid in listening_pids(port):
            if is_fraudlens_listener(pid):
                to_terminate.add(pid)
                ports_to_drain.add(port)
            else:
                blockers.append(f"{port}: pid {pid} ({process_command(pid) or 'unknown'})")
    if blockers and fail_on_blockers:
        details = "; ".join(blockers)
        raise RuntimeError(
            "local demo port(s) are occupied by non-FraudLens processes: "
            f"{details}. Stop them or override BACKEND_PORT/FRONTEND_PORT/POSTGRES_PORT."
        )
    for pid in to_terminate:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
    drain_ports = tuple(sorted(ports_to_drain))
    if to_terminate and not wait_for_ports_to_drain(drain_ports):
        for pid in to_terminate:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        if not wait_for_ports_to_drain(drain_ports):
            raise RuntimeError("FraudLens local listeners did not release their ports in time")
    return blockers


def http_ok(url: str) -> bool:
    """Return True if a single GET to url returns HTTP 200 (within the poll timeout)."""
    try:
        with urllib.request.urlopen(url, timeout=HEALTH_POLL_SECONDS) as response:
            return bool(response.status == HTTP_OK)
    except (urllib.error.URLError, OSError):
        return False


def wait_for_http(url: str, *, timeout: float = HEALTH_TIMEOUT_SECONDS) -> bool:
    """Poll url until it returns HTTP 200 or the timeout elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if http_ok(url):
            return True
        time.sleep(HEALTH_POLL_SECONDS)
    return False


def await_backend_ready(
    base: str, process: subprocess.Popen[bytes], *, timeout: float = HEALTH_TIMEOUT_SECONDS
) -> bool:
    """Wait for /healthz then /readyz==200, bailing FAST if the backend process exits first.

    Hardens the smoke gate (plan §16 Phase 14): a crash-on-boot fails immediately with the exit
    code instead of blocking for the full timeout, and a never-ready /readyz is reported by name.
    """
    deadline = time.monotonic() + timeout
    for path in ("/healthz", "/readyz"):
        while not http_ok(f"{base}{path}"):
            if process.poll() is not None:
                print(
                    f"backend exited (code {process.returncode}) before {path} was ready",
                    file=sys.stderr,
                )
                return False
            if time.monotonic() >= deadline:
                print(f"timed out waiting for {path}", file=sys.stderr)
                return False
            time.sleep(HEALTH_POLL_SECONDS)
    return True
