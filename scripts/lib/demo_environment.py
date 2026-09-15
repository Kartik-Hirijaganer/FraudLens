"""Summary: Non-secret local and live child-process environment construction.

Key classes:
- (none)

Key functions:
- local_database_url: build the local asyncpg URL from config values.
- demo_environment: construct keyless local-demo overrides.
- live_environment: construct Infisical-backed live-mode overrides.

Notes:
- Secret values are inherited from process environment and never authored here.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_STATE_DIR = REPO_ROOT / ".local"
DEFAULTS: dict[str, str] = {
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5432",
    "POSTGRES_USER": "fraudlens",
    "POSTGRES_PASSWORD": "fraudlens",
    "POSTGRES_DB": "fraudlens",
    "DEMO_HOST": "localhost",
    "BACKEND_PORT": "8000",
    "FRONTEND_PORT": "5173",
}
AUTO_PORT_STARTS = {"POSTGRES_PORT": 55432, "BACKEND_PORT": 18000, "FRONTEND_PORT": 15173}
AUTO_PORT_SEARCH_SPAN = 100
MIN_TCP_PORT = 1
MAX_TCP_PORT = 65535


def _env(name: str) -> str:
    """Return an env var, falling back to the documented non-secret local default."""
    return os.environ.get(name, DEFAULTS[name])


def _parse_port(port: str) -> int | None:
    """Parse and validate a TCP port string."""
    if not port.isdigit():
        return None
    value = int(port)
    return value if MIN_TCP_PORT <= value <= MAX_TCP_PORT else None


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
    start = AUTO_PORT_STARTS[name]
    for port in range(start, start + AUTO_PORT_SEARCH_SPAN):
        candidate = str(port)
        if _is_port_available(candidate):
            return candidate
    raise RuntimeError(f"no available fallback port found for {name}")


def _assign_available_default_ports(names: tuple[str, ...]) -> None:
    """Move unset default ports to free fallbacks when another project owns the common ports."""
    for name in names:
        if name in os.environ:
            continue
        requested = DEFAULTS[name]
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
