"""Summary: The one-command local demo orchestrator (plan §3.4 / §16 Phase 1). It boots a
Docker Postgres, migrates and foundation-seeds it, promotes the best locally trained
gates-passed model bundle to ACTIVE (the seeded fixture stays only when none exists),
idempotently fetches the IBM AML-Data source through Infisical, masks and ingests its
representative case pack, builds the offline regulatory index, and batch-investigates the
primary demo tenant through the production pipeline before starting FastAPI
and Vite. The local application remains keyless after download and uses the mock SAR drafter. The
preferred ports are :8000/:5173, with free fallbacks when occupied. `rebuild` drops database
volumes/generated caches while preserving the large gitignored IBM download; `reset` also deletes
the download. `live` uses Infisical-backed Supabase/Postgres/OpenRouter services and provisions the
four demo identities. `smoke` remains the fast foundation-only health/readiness gate.

Key classes:
- (none)

Key functions:
- runner_guard: hold the repository-scoped single-runner lock for stack-starting commands.
- local_database_url: build the local asyncpg URL from (non-secret) env/defaults.
- demo_environment: the dev environment overrides handed to the child processes.
- live_environment: dev-local overrides for real Supabase Auth/Postgres + OpenRouter.
- up: boot Postgres + backend + frontend, print the URL, wait for Ctrl-C.
- live: boot backend + frontend against real services, print the URL, wait for Ctrl-C.
- down: stop the compose stack.
- reset: stop the stack and remove volumes + local state.
- rebuild: reset local state/caches and boot the full stack from a clean seed.
- smoke: boot Postgres + backend, assert the health probes, tear down (gate).
- main: CLI entry; dispatch up/down/reset/rebuild/smoke.

Notes:
- Local Docker credentials are non-secret conveniences. The Kaggle token is injected from
  Infisical only for the fetch child and removed before database, backend, and frontend children.
- Missing IBM data or credentials fail startup; this command never falls back to sample alerts.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = REPO_ROOT / "docker-compose.local.yml"
LOCAL_STATE_DIR = REPO_ROOT / ".local"
FRONTEND_DIR = REPO_ROOT / "frontend"
_RUNNER_LOCK_DIGEST = hashlib.sha256(str(REPO_ROOT.resolve()).encode("utf-8")).hexdigest()[:12]
_RUNNER_LOCK_PATH = Path(tempfile.gettempdir()) / f"fraudlens-local-demo-{_RUNNER_LOCK_DIGEST}.lock"

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
_AUTO_PORT_STARTS: dict[str, int] = {
    "POSTGRES_PORT": 55432,
    "BACKEND_PORT": 18000,
    "FRONTEND_PORT": 15173,
}
_AUTO_PORT_SEARCH_SPAN = 100
_MIN_TCP_PORT = 1
_MAX_TCP_PORT = 65535
_HEALTH_TIMEOUT_SECONDS = 60.0
_HEALTH_POLL_SECONDS = 1.0
_HTTP_OK = 200
_PORT_DRAIN_TIMEOUT_SECONDS = 5.0
_LOCAL_CACHE_PATHS = (
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
_REPO_PROCESS_MARKERS = (
    "fraudlens_backend.main:app",
    "scripts/local_demo.py",
    "npm --prefix frontend run dev",
)
_STACK_COMMANDS = frozenset({"up", "live", "rebuild", "run", "smoke"})


@contextlib.contextmanager
def runner_guard() -> Iterator[object]:
    """Hold a cross-process lock so only one FraudLens local stack can run at a time."""
    with _RUNNER_LOCK_PATH.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                "another FraudLens local stack is already running; stop it with Ctrl-C "
                "before starting run/run-live again"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        try:
            yield handle
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _env(name: str) -> str:
    """Return an env var, falling back to the documented non-secret local default."""
    return os.environ.get(name, _DEFAULTS[name])


def _parse_port(port: str) -> int | None:
    """Parse and validate a TCP port string."""
    if not port.isdigit():
        return None
    value = int(port)
    return value if _MIN_TCP_PORT <= value <= _MAX_TCP_PORT else None


def _is_port_available(port: str) -> bool:
    """Return True when a local TCP port can be bound by the demo."""
    parsed = _parse_port(port)
    if parsed is None:
        return False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((_env("DEMO_HOST"), parsed))
    except OSError:
        return False
    return True


def _first_available_port(name: str) -> str:
    """Find the first available fallback port for a known local-demo port variable."""
    start = _AUTO_PORT_STARTS[name]
    for port in range(start, start + _AUTO_PORT_SEARCH_SPAN):
        candidate = str(port)
        if _is_port_available(candidate):
            return candidate
    raise RuntimeError(f"no available fallback port found for {name}")


def _assign_available_default_ports(names: tuple[str, ...]) -> None:
    """Move unset default ports to free fallbacks when another project owns the common ports."""
    for name in names:
        if name in os.environ:
            continue
        requested = _DEFAULTS[name]
        if _is_port_available(requested):
            continue
        selected = _first_available_port(name)
        os.environ[name] = selected
        print(f">> {name} default {requested} is unavailable; using {selected}", flush=True)


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
    for name in ("POSTGRES_PORT", "BACKEND_PORT", "FRONTEND_PORT"):
        env.setdefault(name, _env(name))
    env.update(
        {
            "FRAUDLENS_ENVIRONMENT": "dev",
            "VITE_AUTH_DEV_BYPASS": "true",
            "VITE_DEMO_AUTH_ENABLED": "false",
            "DATABASE_URL": local_database_url(),
            "FRAUDLENS_STORAGE_BACKEND": "local",
            "FRAUDLENS_QUEUE_BACKEND": "local",
            "FRAUDLENS_LOCAL_JOB_EXECUTE_ON_SUBMIT": "true",
            "FRAUDLENS_ALLOW_CANDIDATE_SCORING_IN_DEV": "false",
            "FRAUDLENS_LLM_MODE": "mock",
            "FRAUDLENS_RAG_EMBEDDING_MODE": "offline",
        }
    )
    frontend_origin = _base_url(env["FRONTEND_PORT"])
    env.setdefault("FRAUDLENS_CORS_ALLOW_ORIGINS", json.dumps([frontend_origin]))
    env.setdefault("VITE_API_BASE_URL", _base_url(env["BACKEND_PORT"]))
    return env


def _supabase_project_url(env: dict[str, str]) -> str:
    """Return the non-secret Supabase project URL from accepted env names."""
    value = (
        env.get("SUPABASE_PROJECT_URL")
        or env.get("SUPABASE_URL")
        or env.get("FRAUDLENS_SUPABASE_URL")
        or env.get("VITE_SUPABASE_URL")
    )
    if not value:
        raise RuntimeError("SUPABASE_URL or SUPABASE_PROJECT_URL is required for live mode")
    return value.rstrip("/")


def _require_live_env(env: dict[str, str]) -> None:
    """Fail fast when run-live is missing required Infisical-injected secrets."""
    required = (
        "DATABASE_URL",
        "OPENROUTER_API_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "VITE_SUPABASE_ANON_KEY",
    )
    missing = [name for name in required if not env.get(name)]
    if missing:
        raise RuntimeError(f"missing live secret env vars: {', '.join(missing)}")


def live_environment() -> dict[str, str]:
    """Return child-process env for local live Supabase Auth/Postgres + OpenRouter."""
    env = dict(os.environ)
    for name in ("BACKEND_PORT", "FRONTEND_PORT"):
        env.setdefault(name, _env(name))
    supabase_url = _supabase_project_url(env)
    _require_live_env(env)
    env.update(
        {
            "FRAUDLENS_ENVIRONMENT": "dev",
            "FRAUDLENS_AUTH_DEV_BYPASS": "false",
            "VITE_AUTH_DEV_BYPASS": "false",
            "VITE_DEMO_AUTH_ENABLED": "true",
            "FRAUDLENS_AUTH_JWKS_URL": f"{supabase_url}/auth/v1/.well-known/jwks.json",
            "FRAUDLENS_AUTH_JWT_ISSUER": f"{supabase_url}/auth/v1",
            "FRAUDLENS_AUTH_JWT_AUDIENCE": "authenticated",
            "FRAUDLENS_AUTH_ROLE_CLAIM": "user_role",
            "FRAUDLENS_SUPABASE_URL": supabase_url,
            "FRAUDLENS_STORAGE_BACKEND": "local",
            "FRAUDLENS_QUEUE_BACKEND": "local",
            "FRAUDLENS_ALLOW_CANDIDATE_SCORING_IN_DEV": "true",
            "FRAUDLENS_LLM_MODE": "live",
            "FRAUDLENS_RAG_EMBEDDING_MODE": "live",
            "VITE_SUPABASE_URL": supabase_url,
        }
    )
    frontend_origin = _base_url(env["FRONTEND_PORT"])
    env.setdefault("FRAUDLENS_CORS_ALLOW_ORIGINS", json.dumps([frontend_origin]))
    env.setdefault("VITE_API_BASE_URL", _base_url(env["BACKEND_PORT"]))
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
    """Delete generated demo/check caches while preserving downloaded IBM AML source data."""
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


def _free_fraudlens_ports(ports: tuple[str, ...], *, fail_on_blockers: bool = True) -> list[str]:
    """Terminate FraudLens-owned listeners and optionally report unrelated port owners."""
    blockers: list[str] = []
    to_terminate: set[int] = set()
    ports_to_drain: set[str] = set()
    for port in ports:
        for pid in _listening_pids(port):
            if _is_fraudlens_listener(pid):
                to_terminate.add(pid)
                ports_to_drain.add(port)
            else:
                blockers.append(f"{port}: pid {pid} ({_process_command(pid) or 'unknown'})")
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
    if to_terminate and not _wait_for_ports_to_drain(drain_ports):
        for pid in to_terminate:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        if not _wait_for_ports_to_drain(drain_ports):
            raise RuntimeError("FraudLens local listeners did not release their ports in time")
    return blockers


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


def _fetch_ibm_demo_data(env: dict[str, str]) -> None:
    """Idempotently fetch/verify the real IBM AML dataset before local demo bootstrap."""
    script = REPO_ROOT / "scripts" / "fetch_dataset.py"
    if not script.is_file():
        raise RuntimeError(
            "IBM AML fetch script is missing; local demo cannot fall back to samples"
        )
    subprocess.run(
        ["uv", "run", "python", "scripts/fetch_dataset.py", "--source", "ibm-aml"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )


def _ingest_ibm_demo_data(env: dict[str, str]) -> None:
    """Ingest a bounded, masked IBM AML partition into the freshly migrated local database."""
    script = REPO_ROOT / "scripts" / "ingest_aml_demo.py"
    if not script.is_file():
        raise RuntimeError("IBM AML demo ingest script is missing")
    subprocess.run(
        ["uv", "run", "python", "scripts/ingest_aml_demo.py"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )


def _activate_trained_model(env: dict[str, str]) -> None:
    """Promote the best locally trained gates-passed bundle to ACTIVE (fixture stays otherwise).

    A gates-failed or absent bundle is never promoted; the script prints the honest outcome and
    the seeded fixture keeps serving, so a fresh clone still boots.
    """
    subprocess.run(
        ["uv", "run", "python", "scripts/activate_model.py"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )


def _score_ibm_demo_data(env: dict[str, str]) -> None:
    """Batch-investigate the primary IBM demo partition through the production pipeline."""
    subprocess.run(
        ["uv", "run", "python", "-m", "fraudlens_backend.jobs.runner"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )


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


def _frontend_command(env: dict[str, str]) -> list[str]:
    """Build the Vite command for the SPA, pinning the selected local port."""
    return [
        "npm",
        "--prefix",
        "frontend",
        "run",
        "dev",
        "--",
        "--host",
        env.get("DEMO_HOST", _DEFAULTS["DEMO_HOST"]),
        "--port",
        env.get("FRONTEND_PORT", _DEFAULTS["FRONTEND_PORT"]),
        "--strictPort",
    ]


def up() -> int:
    """Fetch IBM data, rebuild evidence through the pipeline, then boot backend + frontend."""
    _require_tools("docker", "uv", "npm")
    _assign_available_default_ports(("POSTGRES_PORT", "BACKEND_PORT", "FRONTEND_PORT"))
    env = demo_environment()
    backend_port, frontend_port = env["BACKEND_PORT"], env["FRONTEND_PORT"]
    _fetch_ibm_demo_data(env)
    # The downloader is the only child that may receive the Kaggle credential.
    env.pop("KAGGLE_API_TOKEN", None)
    _start_postgres(env)
    _maybe_migrate_and_seed(env)
    _activate_trained_model(env)
    _ingest_ibm_demo_data(env)
    _maybe_build_rag_index(env)
    _score_ibm_demo_data(env)
    procs = [
        subprocess.Popen(_backend_command(env), cwd=REPO_ROOT, env=env),
        subprocess.Popen(_frontend_command(env), cwd=REPO_ROOT, env=env),
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


def live() -> int:
    """Boot backend + frontend against live Supabase/Postgres/OpenRouter services."""
    _require_tools("uv", "npm")
    _assign_available_default_ports(("BACKEND_PORT", "FRONTEND_PORT"))
    env = live_environment()
    backend_port, frontend_port = env["BACKEND_PORT"], env["FRONTEND_PORT"]
    _provision_live_demo_auth(env)
    procs = [
        subprocess.Popen(_backend_command(env), cwd=REPO_ROOT, env=env),
        subprocess.Popen(_frontend_command(env), cwd=REPO_ROOT, env=env),
    ]
    try:
        if not _wait_for_http(f"{_base_url(backend_port)}/healthz"):
            print("backend did not become healthy in time", file=sys.stderr)
            return 1
        print(f"\nFraudLens live-local is up — open {_base_url(frontend_port)}")
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


def _provision_live_demo_auth(env: dict[str, str]) -> None:
    """Provision real demo identities before exposing the live-local login screen."""
    print(">> ensuring live demo Supabase users", flush=True)
    subprocess.run(
        ["uv", "run", "python", "scripts/provision_demo_auth.py"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )


def down() -> int:
    """Stop the compose stack (containers removed, volumes kept)."""
    _require_tools("docker")
    _compose_down(remove_volumes=False)
    return 0


def reset() -> int:
    """Stop the stack, drop its volumes, and remove all local state including IBM source data."""
    _require_tools("docker")
    _compose_down(remove_volumes=True)
    _clear_local_caches()
    _remove_path(LOCAL_STATE_DIR)
    return 0


def rebuild() -> int:
    """Reset Docker/generated caches, preserve IBM source data, then boot the real-data demo."""
    _require_tools("docker", "uv", "npm")
    ports = (_env("POSTGRES_PORT"), _env("BACKEND_PORT"), _env("FRONTEND_PORT"))
    print(">> stopping FraudLens local Docker stack and dropping volumes")
    _compose_down(remove_volumes=True)
    print(">> clearing local generated state and caches")
    _clear_local_caches()
    print(">> freeing FraudLens-owned local ports")
    _free_fraudlens_ports(ports, fail_on_blockers=False)
    _assign_available_default_ports(("BACKEND_PORT", "FRONTEND_PORT"))
    ports = (_env("POSTGRES_PORT"), _env("BACKEND_PORT"), _env("FRONTEND_PORT"))
    _free_fraudlens_ports(ports)
    return up()


def smoke() -> int:
    """Headless gate: boot Postgres + backend, assert /healthz + /readyz, tear down."""
    _require_tools("docker", "uv")
    _assign_available_default_ports(("POSTGRES_PORT", "BACKEND_PORT"))
    env = demo_environment()
    backend_port = env["BACKEND_PORT"]
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
    "live": live,
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
    try:
        if args.command in _STACK_COMMANDS:
            with runner_guard():
                return _COMMANDS[args.command]()
        return _COMMANDS[args.command]()
    except RuntimeError as exc:
        print(f"local demo failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
