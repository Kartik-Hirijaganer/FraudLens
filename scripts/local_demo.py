"""Summary: The one-command local demo orchestrator (plan §3.4 / §16 Phase 1). It
boots the full stack with NO cloud and NO secrets: a docker-compose Postgres, then the
gateway+services (uvicorn :8000) and the frontend (Vite :5173), wired to local backends
and the keyless mock SAR drafter via dev config. `up` waits for /healthz, prints the
demo URL, and blocks until Ctrl-C, then tears the child processes down cleanly; `down`
/`reset` stop the stack (reset also drops volumes + .local state); `rebuild` performs a
clean local reset, clears generated caches, frees FraudLens-owned listeners, then boots;
`smoke` is the headless gate — boot Postgres + backend, assert /healthz and /readyz, tear down. The
database migrate + seed + RAG-index-build steps are guarded so they run once their phases
land (Alembic/seed in Phase 2, the FinCEN/BSA index in Phase 6), and are skipped (not
failed) until then, keeping `make local-demo` green and shipping a fixture RAG index.

Key classes:
- (none)

Key functions:
- local_database_url: build the local asyncpg URL from (non-secret) env/defaults.
- demo_environment: the dev environment overrides handed to the child processes.
- up: boot Postgres + backend + frontend, print the URL, wait for Ctrl-C.
- down: stop the compose stack.
- reset: stop the stack and remove volumes + local state.
- rebuild: reset local state/caches and boot the full stack from a clean seed.
- smoke: boot Postgres + backend, assert the health probes, tear down (gate).
- main: CLI entry; dispatch up/down/reset/rebuild/smoke.

Notes:
- All credentials here are NON-SECRET local docker conveniences (overridable via .env);
  real secrets always come from Infisical at runtime (Golden Rule 2).
"""

from __future__ import annotations

import argparse
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

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = REPO_ROOT / "docker-compose.local.yml"
LOCAL_STATE_DIR = REPO_ROOT / ".local"
FRONTEND_DIR = REPO_ROOT / "frontend"

# Non-secret local defaults (overridable via .env / environment); see .env.example.
_DEFAULTS: dict[str, str] = {
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5432",
    "POSTGRES_USER": "fraudlens",
    "POSTGRES_PASSWORD": "fraudlens",
    "POSTGRES_DB": "fraudlens",
    "DEMO_HOST": "localhost",
    "BACKEND_PORT": "8000",
    "FRONTEND_PORT": "5173",
}
_HEALTH_TIMEOUT_SECONDS = 60.0
_HEALTH_POLL_SECONDS = 1.0
_HTTP_OK = 200
_PORT_DRAIN_TIMEOUT_SECONDS = 5.0
_LOCAL_CACHE_PATHS = (
    LOCAL_STATE_DIR,
    REPO_ROOT / ".pytest_cache",
    REPO_ROOT / ".ruff_cache",
    REPO_ROOT / ".mypy_cache",
    FRONTEND_DIR / "node_modules" / ".vite",
    FRONTEND_DIR / "coverage",
    FRONTEND_DIR / "dist",
    REPO_ROOT / "coverage.xml",
)
_REPO_PROCESS_MARKERS = (
    "fraudlens_backend.main:app",
    "scripts/local_demo.py",
    "npm --prefix frontend run dev",
)


def _env(name: str) -> str:
    """Return an env var, falling back to the documented non-secret local default."""
    return os.environ.get(name, _DEFAULTS[name])


def local_database_url() -> str:
    """Build the local async (asyncpg) database URL from env/defaults."""
    user, password = _env("POSTGRES_USER"), _env("POSTGRES_PASSWORD")
    host, port, name = _env("POSTGRES_HOST"), _env("POSTGRES_PORT"), _env("POSTGRES_DB")
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{name}"


def _base_url(port: str) -> str:
    """Build a local base URL from the (config-driven) demo host + a port."""
    return f"http://{_env('DEMO_HOST')}:{port}"


def demo_environment() -> dict[str, str]:
    """Return the child-process environment: dev config, local backends, mock LLM."""
    env = dict(os.environ)
    env.update(
        {
            "FRAUDLENS_ENVIRONMENT": "dev",
            "DATABASE_URL": local_database_url(),
            "FRAUDLENS_STORAGE_BACKEND": "local",
            "FRAUDLENS_QUEUE_BACKEND": "local",
            "FRAUDLENS_LLM_MODE": "mock",
        }
    )
    env.setdefault("VITE_API_BASE_URL", _base_url(env.get("BACKEND_PORT", _env("BACKEND_PORT"))))
    return env


def _compose(*args: str) -> list[str]:
    """Build a `docker compose -f <file> ...` command for the local stack."""
    return ["docker", "compose", "-f", str(COMPOSE_FILE), *args]


def _require_tools(*tools: str) -> None:
    """Raise a clear error if any required CLI tool is not on PATH."""
    missing = [tool for tool in tools if shutil.which(tool) is None]
    if missing:
        raise RuntimeError(f"missing required tools: {', '.join(missing)}")


def _compose_down(*, remove_volumes: bool) -> None:
    """Stop the FraudLens compose stack, optionally dropping volumes too."""
    args = ["down", "--remove-orphans"]
    if remove_volumes:
        args.append("-v")
    subprocess.run(_compose(*args), cwd=REPO_ROOT, check=True)


def _remove_path(path: Path) -> None:
    """Remove a generated local path when present (directory or file)."""
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _clear_local_caches() -> None:
    """Delete generated local state/caches used by the demo and checks."""
    for path in _LOCAL_CACHE_PATHS:
        _remove_path(path)


def _listening_pids(port: str) -> list[int]:
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


def _process_command(pid: int) -> str:
    """Return a process command line, best-effort."""
    proc = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _process_cwd(pid: int) -> Path | None:
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


def _is_under_repo(path: Path | None) -> bool:
    """Return True when path resolves under the FraudLens repository."""
    if path is None:
        return False
    with contextlib.suppress(OSError, RuntimeError):
        return path.resolve().is_relative_to(REPO_ROOT.resolve())
    return False


def _is_fraudlens_listener(pid: int) -> bool:
    """Return True when a listener is a FraudLens-owned local dev process."""
    if pid == os.getpid():
        return False
    command = _process_command(pid)
    if str(REPO_ROOT) in command or any(marker in command for marker in _REPO_PROCESS_MARKERS):
        return True
    return _is_under_repo(_process_cwd(pid))


def _wait_for_ports_to_drain(ports: tuple[str, ...]) -> bool:
    """Wait briefly for all configured ports to have no remaining listeners."""
    deadline = time.monotonic() + _PORT_DRAIN_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if not any(_listening_pids(port) for port in ports):
            return True
        time.sleep(_HEALTH_POLL_SECONDS)
    return not any(_listening_pids(port) for port in ports)


def _free_fraudlens_ports(ports: tuple[str, ...]) -> None:
    """Terminate FraudLens-owned listeners and fail clearly on unrelated port owners."""
    blockers: list[str] = []
    to_terminate: set[int] = set()
    for port in ports:
        for pid in _listening_pids(port):
            if _is_fraudlens_listener(pid):
                to_terminate.add(pid)
            else:
                blockers.append(f"{port}: pid {pid} ({_process_command(pid) or 'unknown'})")
    if blockers:
        details = "; ".join(blockers)
        raise RuntimeError(
            "local demo port(s) are occupied by non-FraudLens processes: "
            f"{details}. Stop them or override BACKEND_PORT/FRONTEND_PORT/POSTGRES_PORT."
        )
    for pid in to_terminate:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
    if to_terminate and not _wait_for_ports_to_drain(ports):
        for pid in to_terminate:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        if not _wait_for_ports_to_drain(ports):
            raise RuntimeError("FraudLens local listeners did not release their ports in time")


def _http_ok(url: str) -> bool:
    """Return True if a single GET to url returns HTTP 200 (within the poll timeout)."""
    try:
        with urllib.request.urlopen(url, timeout=_HEALTH_POLL_SECONDS) as response:
            return bool(response.status == _HTTP_OK)
    except (urllib.error.URLError, OSError):
        return False


def _wait_for_http(url: str, *, timeout: float = _HEALTH_TIMEOUT_SECONDS) -> bool:
    """Poll url until it returns HTTP 200 or the timeout elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _http_ok(url):
            return True
        time.sleep(_HEALTH_POLL_SECONDS)
    return False


def _await_backend_ready(
    base: str, process: subprocess.Popen[bytes], *, timeout: float = _HEALTH_TIMEOUT_SECONDS
) -> bool:
    """Wait for /healthz then /readyz==200, bailing FAST if the backend process exits first.

    Hardens the smoke gate (plan §16 Phase 14): a crash-on-boot fails immediately with the exit
    code instead of blocking for the full timeout, and a never-ready /readyz is reported by name.
    """
    deadline = time.monotonic() + timeout
    for path in ("/healthz", "/readyz"):
        while not _http_ok(f"{base}{path}"):
            if process.poll() is not None:
                print(
                    f"backend exited (code {process.returncode}) before {path} was ready",
                    file=sys.stderr,
                )
                return False
            if time.monotonic() >= deadline:
                print(f"timed out waiting for {path}", file=sys.stderr)
                return False
            time.sleep(_HEALTH_POLL_SECONDS)
    return True


def _start_postgres(env: dict[str, str]) -> None:
    """Start the compose Postgres in the background and wait for it to be healthy."""
    subprocess.run(_compose("up", "-d", "--wait"), cwd=REPO_ROOT, env=env, check=True)


def _maybe_migrate_and_seed(env: dict[str, str]) -> None:
    """Run migrations + seed when they exist; skip (don't fail) until Phase 2 lands them."""
    if (REPO_ROOT / "alembic.ini").is_file():
        subprocess.run(
            ["uv", "run", "alembic", "upgrade", "head"], cwd=REPO_ROOT, env=env, check=True
        )
    else:
        print(">> migrations: skipped (Alembic config lands in Phase 2)")
    if (REPO_ROOT / "scripts" / "seed.py").is_file():
        subprocess.run(
            ["uv", "run", "python", "scripts/seed.py"], cwd=REPO_ROOT, env=env, check=True
        )
    else:
        print(">> seed: skipped (scripts/seed.py lands in Phase 2)")


def _maybe_build_rag_index(env: dict[str, str]) -> None:
    """Build the FinCEN/BSA RAG index when present; skip (don't fail) until Phase 6 lands it."""
    if (REPO_ROOT / "scripts" / "ingest_rag.py").is_file():
        subprocess.run(
            ["uv", "run", "python", "scripts/ingest_rag.py"], cwd=REPO_ROOT, env=env, check=True
        )
    else:
        print(">> rag index: skipped (scripts/ingest_rag.py lands in Phase 6)")


def _backend_command(env: dict[str, str]) -> list[str]:
    """Build the uvicorn command for the gateway+services app."""
    return [
        "uv",
        "run",
        "uvicorn",
        "fraudlens_backend.main:app",
        "--host",
        "localhost",
        "--port",
        env.get("BACKEND_PORT", _DEFAULTS["BACKEND_PORT"]),
    ]


def up() -> int:
    """Boot Postgres + backend + frontend, print the demo URL, wait for Ctrl-C."""
    _require_tools("docker", "uv", "npm")
    env = demo_environment()
    backend_port, frontend_port = _env("BACKEND_PORT"), _env("FRONTEND_PORT")
    _start_postgres(env)
    _maybe_migrate_and_seed(env)
    _maybe_build_rag_index(env)
    procs = [
        subprocess.Popen(_backend_command(env), cwd=REPO_ROOT, env=env),
        subprocess.Popen(["npm", "--prefix", "frontend", "run", "dev"], cwd=REPO_ROOT, env=env),
    ]
    try:
        if not _wait_for_http(f"{_base_url(backend_port)}/healthz"):
            print("backend did not become healthy in time", file=sys.stderr)
            return 1
        print(f"\nFraudLens local demo is up — open {_base_url(frontend_port)}")
        print(f"gateway/API: {_base_url(backend_port)}  (Ctrl-C to stop)\n")
        signal.pause()
    except KeyboardInterrupt:
        print("\nshutting down…")
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=10)
    return 0


def down() -> int:
    """Stop the compose stack (containers removed, volumes kept)."""
    _require_tools("docker")
    _compose_down(remove_volumes=False)
    return 0


def reset() -> int:
    """Stop the stack, drop its volumes, and remove local state (.local/)."""
    _require_tools("docker")
    _compose_down(remove_volumes=True)
    _clear_local_caches()
    return 0


def rebuild() -> int:
    """Reset local Docker/state/caches, free local FraudLens ports, then boot the stack."""
    _require_tools("docker", "uv", "npm")
    ports = (_env("POSTGRES_PORT"), _env("BACKEND_PORT"), _env("FRONTEND_PORT"))
    print(">> stopping FraudLens local Docker stack and dropping volumes")
    _compose_down(remove_volumes=True)
    print(">> clearing local generated state and caches")
    _clear_local_caches()
    print(">> freeing FraudLens-owned local ports")
    _free_fraudlens_ports(ports)
    return up()


def smoke() -> int:
    """Headless gate: boot Postgres + backend, assert /healthz + /readyz, tear down."""
    _require_tools("docker", "uv")
    env = demo_environment()
    backend_port = _env("BACKEND_PORT")
    _start_postgres(env)
    _maybe_migrate_and_seed(env)
    _maybe_build_rag_index(env)
    backend = subprocess.Popen(_backend_command(env), cwd=REPO_ROOT, env=env)
    try:
        ok = _await_backend_ready(_base_url(backend_port), backend)
        print("local-demo smoke:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        backend.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            backend.wait(timeout=10)
        subprocess.run(_compose("down", "--remove-orphans"), cwd=REPO_ROOT, check=False)


_COMMANDS = {
    "up": up,
    "down": down,
    "rebuild": rebuild,
    "reset": reset,
    "run": rebuild,
    "smoke": smoke,
}


def main(argv: list[str] | None = None) -> int:
    """Parse the subcommand and dispatch to the matching handler."""
    parser = argparse.ArgumentParser(description="FraudLens one-command local demo.")
    parser.add_argument("command", choices=sorted(_COMMANDS), help="demo lifecycle action")
    args = parser.parse_args(argv)
    return _COMMANDS[args.command]()


if __name__ == "__main__":
    raise SystemExit(main())
