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
configured demo identities without touching data; `live-demo` is the portfolio path — the same live
services, plus migrate, seed, persona provisioning, the story's RAG index, and the pinned portfolio
demo story. `smoke` remains the fast foundation-only health/readiness gate.

Key classes:
- (none)

Key functions:
- runner_guard: hold the repository-scoped single-runner lock for stack-starting commands.
- up: boot Postgres + backend + frontend, print the URL, wait for Ctrl-C.
- live: boot backend + frontend against real services, print the URL, wait for Ctrl-C.
- live_demo: boot the live stack AND apply the configured portfolio demo story.
- down: stop the compose stack.
- reset: stop the stack and remove volumes + local state.
- rebuild: reset local state/caches and boot the full stack from a clean seed.
- smoke: boot Postgres + backend, assert the health probes, tear down (gate).
- main: CLI entry; dispatch up/down/live/live-demo/reset/rebuild/smoke.

Notes:
- Local Docker credentials are non-secret conveniences. The Kaggle token is injected from
Infisical only for the fetch child and removed before database, backend, and frontend children.
- Missing IBM data or credentials fail startup; this command never falls back to sample alerts.
- `live` stays read-only: only `live-demo` migrates, seeds, or writes story rows. A database that
already holds the IBM case pack is NOT converted automatically — the bootstrap refuses a tenant
carrying rows outside the story and tells the operator to run `make portfolio-demo-reset`.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import os
import signal
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

from lib.demo_dataset_steps import (
    activate_trained_model as _activate_trained_model,
)
from lib.demo_dataset_steps import (
    backend_command as _backend_command,
)
from lib.demo_dataset_steps import (
    bootstrap_portfolio_demo as _bootstrap_portfolio_demo,
)
from lib.demo_dataset_steps import (
    build_rag_index as _build_rag_index,
)
from lib.demo_dataset_steps import (
    fetch_ibm_demo_data as _fetch_ibm_demo_data,
)
from lib.demo_dataset_steps import (
    frontend_command as _frontend_command,
)
from lib.demo_dataset_steps import (
    ingest_ibm_demo_data as _ingest_ibm_demo_data,
)
from lib.demo_dataset_steps import (
    migrate_and_seed as _migrate_and_seed,
)
from lib.demo_dataset_steps import (
    portfolio_story_environment as _portfolio_story_environment,
)
from lib.demo_dataset_steps import (
    score_ibm_demo_data as _score_ibm_demo_data,
)
from lib.demo_dataset_steps import (
    start_postgres as _start_postgres,
)
from lib.demo_environment import (
    _assign_available_default_ports,
    _base_url,
    _env,
    _is_port_available,
    demo_environment,
    live_environment,
    local_database_url,
)
from lib.demo_processes import (
    await_backend_ready as _await_backend_ready,
)
from lib.demo_processes import (
    clear_local_caches as _clear_local_caches,
)
from lib.demo_processes import (
    compose_command as _compose,
)
from lib.demo_processes import (
    compose_down as _compose_down,
)
from lib.demo_processes import (
    free_fraudlens_ports as _free_fraudlens_ports,
)
from lib.demo_processes import http_ok as _http_ok
from lib.demo_processes import remove_path as _remove_path
from lib.demo_processes import (
    require_tools as _require_tools,
)
from lib.demo_processes import (
    wait_for_http as _wait_for_http,
)

__all__ = [
    "_activate_trained_model",
    "_assign_available_default_ports",
    "_await_backend_ready",
    "_backend_command",
    "_base_url",
    "_bootstrap_portfolio_demo",
    "_build_rag_index",
    "_clear_local_caches",
    "_compose",
    "_compose_down",
    "_env",
    "_fetch_ibm_demo_data",
    "_free_fraudlens_ports",
    "_frontend_command",
    "_http_ok",
    "_ingest_ibm_demo_data",
    "_is_port_available",
    "_migrate_and_seed",
    "_portfolio_story_environment",
    "_remove_path",
    "_require_tools",
    "_score_ibm_demo_data",
    "_start_postgres",
    "_wait_for_http",
    "demo_environment",
    "down",
    "live",
    "live_demo",
    "local_database_url",
    "main",
    "rebuild",
    "reset",
    "runner_guard",
    "smoke",
    "up",
]


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
_STACK_COMMANDS = frozenset({"up", "live", "live-demo", "rebuild", "run", "smoke"})


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


def _serve(env: dict[str, str], *, banner: str) -> int:
    """Boot backend + frontend, print the URL banner every caller advertises, wait for Ctrl-C."""
    backend_port, frontend_port = env["BACKEND_PORT"], env["FRONTEND_PORT"]
    procs = [
        subprocess.Popen(_backend_command(env), cwd=REPO_ROOT, env=env),
        subprocess.Popen(_frontend_command(env), cwd=REPO_ROOT, env=env),
    ]
    try:
        if not _wait_for_http(f"{_base_url(backend_port)}/healthz"):
            print("backend did not become healthy in time", file=sys.stderr)
            return 1
        print(f"\n{banner} — open {_base_url(frontend_port)}")
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


def up() -> int:
    """Fetch IBM data, rebuild evidence through the pipeline, then boot backend + frontend."""
    _require_tools("docker", "uv", "npm")
    _assign_available_default_ports(("POSTGRES_PORT", "BACKEND_PORT", "FRONTEND_PORT"))
    env = demo_environment()
    _fetch_ibm_demo_data(env)
    # The downloader is the only child that may receive the Kaggle credential.
    env.pop("KAGGLE_API_TOKEN", None)
    _start_postgres(env)
    _migrate_and_seed(env)
    _activate_trained_model(env)
    _ingest_ibm_demo_data(env)
    _build_rag_index(env)
    _score_ibm_demo_data(env)
    return _serve(env, banner="FraudLens local demo is up")


def live() -> int:
    """Boot backend + frontend against live Supabase/Postgres/OpenRouter services (no writes)."""
    _require_tools("uv", "npm")
    _assign_available_default_ports(("BACKEND_PORT", "FRONTEND_PORT"))
    env = live_environment()
    _provision_live_demo_auth(env)
    return _serve(env, banner="FraudLens live-local is up")


def live_demo() -> int:
    """Boot the live stack AND apply the exact configured portfolio demo story.

    `live` only provisions identities; this command is the portfolio path, so it additionally
    migrates, foundation-seeds, provisions the configured personas, builds the story's RAG index,
    and applies the pinned story before the servers start. Converting a database that already
    holds the IBM case pack is deliberately NOT automatic: the bootstrap refuses a tenant carrying
    rows outside the story and names `--reset` (`make portfolio-demo-reset`) as the explicit
    operator action.
    """
    _require_tools("uv", "npm")
    _assign_available_default_ports(("BACKEND_PORT", "FRONTEND_PORT"))
    env = _portfolio_story_environment(live_environment())
    _migrate_and_seed(env)
    _provision_live_demo_auth(env)
    _build_rag_index(env)
    _bootstrap_portfolio_demo(env)
    return _serve(env, banner="FraudLens portfolio demo is up")


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
    _migrate_and_seed(env)
    _build_rag_index(env)
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
    "live-demo": live_demo,
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
