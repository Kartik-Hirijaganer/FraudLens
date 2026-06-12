"""Summary: The one-command local demo orchestrator (plan §3.4 / §16 Phase 1). It
boots the full stack with NO cloud and NO secrets: a docker-compose Postgres, then the
gateway+services (uvicorn :8000) and the frontend (Vite :5173), wired to local backends
and the keyless mock SAR drafter via dev config. `up` waits for /healthz, prints the
demo URL, and blocks until Ctrl-C, then tears the child processes down cleanly; `down`
/`reset` stop the stack (reset also drops volumes + .local state); `smoke` is the
headless gate — boot Postgres + backend, assert /healthz and /readyz, tear down. The
database migrate + seed steps are guarded so they run once Phase 2 lands Alembic + the
seed script, and are skipped (not failed) until then, keeping `make local-demo` green.

Key classes:
- (none)

Key functions:
- local_database_url: build the local asyncpg URL from (non-secret) env/defaults.
- demo_environment: the dev environment overrides handed to the child processes.
- up: boot Postgres + backend + frontend, print the URL, wait for Ctrl-C.
- down: stop the compose stack.
- reset: stop the stack and remove volumes + local state.
- smoke: boot Postgres + backend, assert the health probes, tear down (gate).
- main: CLI entry; dispatch up/down/reset/smoke.

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
    return env


def _compose(*args: str) -> list[str]:
    """Build a `docker compose -f <file> ...` command for the local stack."""
    return ["docker", "compose", "-f", str(COMPOSE_FILE), *args]


def _require_tools(*tools: str) -> None:
    """Raise a clear error if any required CLI tool is not on PATH."""
    missing = [tool for tool in tools if shutil.which(tool) is None]
    if missing:
        raise RuntimeError(f"missing required tools: {', '.join(missing)}")


def _wait_for_http(url: str, *, timeout: float = _HEALTH_TIMEOUT_SECONDS) -> bool:
    """Poll url until it returns HTTP 200 or the timeout elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=_HEALTH_POLL_SECONDS) as response:
                if response.status == _HTTP_OK:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(_HEALTH_POLL_SECONDS)
    return False


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
    subprocess.run(_compose("down"), cwd=REPO_ROOT, check=True)
    return 0


def reset() -> int:
    """Stop the stack, drop its volumes, and remove local state (.local/)."""
    _require_tools("docker")
    subprocess.run(_compose("down", "-v"), cwd=REPO_ROOT, check=True)
    if LOCAL_STATE_DIR.exists():
        shutil.rmtree(LOCAL_STATE_DIR)
    return 0


def smoke() -> int:
    """Headless gate: boot Postgres + backend, assert /healthz + /readyz, tear down."""
    _require_tools("docker", "uv")
    env = demo_environment()
    backend_port = _env("BACKEND_PORT")
    _start_postgres(env)
    _maybe_migrate_and_seed(env)
    backend = subprocess.Popen(_backend_command(env), cwd=REPO_ROOT, env=env)
    try:
        base = _base_url(backend_port)
        ok = _wait_for_http(f"{base}/healthz") and _wait_for_http(f"{base}/readyz")
        print("local-demo smoke:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        backend.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            backend.wait(timeout=10)
        subprocess.run(_compose("down"), cwd=REPO_ROOT, check=False)


_COMMANDS = {"up": up, "down": down, "reset": reset, "smoke": smoke}


def main(argv: list[str] | None = None) -> int:
    """Parse the subcommand and dispatch to the matching handler."""
    parser = argparse.ArgumentParser(description="FraudLens one-command local demo.")
    parser.add_argument("command", choices=sorted(_COMMANDS), help="demo lifecycle action")
    args = parser.parse_args(argv)
    return _COMMANDS[args.command]()


if __name__ == "__main__":
    raise SystemExit(main())
