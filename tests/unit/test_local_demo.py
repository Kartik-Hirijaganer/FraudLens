"""Unit tests for local-demo orchestration, IBM bootstrap, cache reset, and command dispatch."""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

import local_demo
from fraudlens_backend.portfolio_demo import load_portfolio_demo_config


def test_local_database_url_uses_async_driver_and_defaults() -> None:
    url = local_demo.local_database_url()
    assert url == "postgresql+asyncpg://fraudlens:fraudlens@localhost:5432/fraudlens"


def test_local_database_url_honors_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "db.internal")
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    assert "@db.internal:6543/" in local_demo.local_database_url()


def test_demo_environment_selects_local_backends_and_mock_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BACKEND_PORT", raising=False)
    monkeypatch.delenv("FRONTEND_PORT", raising=False)
    env = local_demo.demo_environment()
    assert env["FRAUDLENS_ENVIRONMENT"] == "dev"
    assert env["VITE_AUTH_DEV_BYPASS"] == "true"
    assert env["VITE_DEMO_AUTH_ENABLED"] == "false"
    assert env["FRAUDLENS_STORAGE_BACKEND"] == "local"
    assert env["FRAUDLENS_QUEUE_BACKEND"] == "local"
    assert env["FRAUDLENS_ALLOW_CANDIDATE_SCORING_IN_DEV"] == "false"
    assert env["FRAUDLENS_LLM_MODE"] == "mock"
    assert env["FRAUDLENS_RAG_EMBEDDING_MODE"] == "offline"
    assert env["VITE_API_BASE_URL"] == "http://localhost:8000"
    assert env["FRAUDLENS_CORS_ALLOW_ORIGINS"] == '["http://localhost:5173"]'
    assert env["DATABASE_URL"].startswith("postgresql+asyncpg://")


def test_live_environment_selects_real_auth_db_and_live_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BACKEND_PORT", raising=False)
    monkeypatch.delenv("FRONTEND_PORT", raising=False)
    monkeypatch.setenv("SUPABASE_PROJECT_URL", "https://project.supabase.test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db.test/fraudlens")
    monkeypatch.setenv("OPENROUTER_API_KEY", "placeholder-openrouter-key")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "placeholder-service-role")
    monkeypatch.setenv("VITE_SUPABASE_ANON_KEY", "placeholder-anon-key")
    env = local_demo.live_environment()
    assert env["FRAUDLENS_ENVIRONMENT"] == "dev"
    assert env["FRAUDLENS_AUTH_DEV_BYPASS"] == "false"
    assert env["VITE_AUTH_DEV_BYPASS"] == "false"
    assert env["VITE_DEMO_AUTH_ENABLED"] == "true"
    assert env["FRAUDLENS_AUTH_JWKS_URL"] == (
        "https://project.supabase.test/auth/v1/.well-known/jwks.json"
    )
    assert env["FRAUDLENS_AUTH_JWT_ISSUER"] == "https://project.supabase.test/auth/v1"
    assert env["FRAUDLENS_AUTH_JWT_AUDIENCE"] == "authenticated"
    assert env["FRAUDLENS_AUTH_ROLE_CLAIM"] == "user_role"
    assert env["FRAUDLENS_ALLOW_CANDIDATE_SCORING_IN_DEV"] == "true"
    assert env["FRAUDLENS_LLM_MODE"] == "live"
    assert env["FRAUDLENS_RAG_EMBEDDING_MODE"] == "live"
    assert env["VITE_SUPABASE_URL"] == "https://project.supabase.test"


def test_live_environment_requires_supabase_project_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_PROJECT_URL", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("FRAUDLENS_SUPABASE_URL", raising=False)
    monkeypatch.delenv("VITE_SUPABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db.test/fraudlens")
    monkeypatch.setenv("OPENROUTER_API_KEY", "placeholder-openrouter-key")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "placeholder-service-role")
    monkeypatch.setenv("VITE_SUPABASE_ANON_KEY", "placeholder-anon-key")
    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        local_demo.live_environment()


def test_live_environment_accepts_infisical_supabase_url_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SUPABASE_PROJECT_URL", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db.test/fraudlens")
    monkeypatch.setenv("OPENROUTER_API_KEY", "placeholder-openrouter-key")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "placeholder-service-role")
    monkeypatch.setenv("VITE_SUPABASE_ANON_KEY", "placeholder-anon-key")
    assert local_demo.live_environment()["VITE_SUPABASE_URL"] == "https://project.supabase.test"


def test_compose_command_targets_the_local_file() -> None:
    cmd = local_demo._compose("up", "-d")
    assert cmd[:3] == ["docker", "compose", "-f"]
    assert cmd[-2:] == ["up", "-d"]
    assert cmd[3].endswith("docker-compose.local.yml")


def test_base_url_is_built_from_host_and_port() -> None:
    assert local_demo._base_url("8000") == "http://localhost:8000"


def test_assign_available_default_ports_uses_fallback_when_default_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BACKEND_PORT", raising=False)
    monkeypatch.setattr(local_demo, "_is_port_available", lambda port: port == "18000")
    local_demo._assign_available_default_ports(("BACKEND_PORT",))
    assert local_demo._env("BACKEND_PORT") == "18000"


def test_assign_available_default_ports_honors_explicit_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BACKEND_PORT", "19000")
    monkeypatch.setattr(local_demo, "_is_port_available", lambda _port: False)
    local_demo._assign_available_default_ports(("BACKEND_PORT",))
    assert local_demo._env("BACKEND_PORT") == "19000"


def test_frontend_command_pins_selected_port() -> None:
    cmd = local_demo._frontend_command({"DEMO_HOST": "localhost", "FRONTEND_PORT": "15173"})
    assert cmd[-5:] == ["--host", "localhost", "--port", "15173", "--strictPort"]


def test_main_dispatches_to_the_named_command(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    @contextlib.contextmanager
    def guard():
        calls.append("guard")
        yield

    monkeypatch.setattr(local_demo, "runner_guard", guard)
    for name in ("up", "down", "live", "live-demo", "rebuild", "reset", "run", "smoke"):
        monkeypatch.setitem(local_demo._COMMANDS, name, lambda n=name: calls.append(n) or 0)
    assert local_demo.main(["down"]) == 0
    assert local_demo.main(["live"]) == 0
    assert local_demo.main(["live-demo"]) == 0
    assert local_demo.main(["smoke"]) == 0
    assert local_demo.main(["run"]) == 0
    assert calls == [
        "down",
        "guard",
        "live",
        "guard",
        "live-demo",
        "guard",
        "smoke",
        "guard",
        "run",
    ]


def test_runner_guard_rejects_a_competing_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(_fd: int, _operation: int) -> None:
        raise BlockingIOError

    monkeypatch.setattr(local_demo.fcntl, "flock", blocked)
    with (
        pytest.raises(RuntimeError, match="another FraudLens local stack"),
        local_demo.runner_guard(),
    ):
        pytest.fail("the competing lock must fail before entering")


def test_main_reports_runner_errors_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    @contextlib.contextmanager
    def blocked_guard():
        raise RuntimeError("another FraudLens local stack is already running")
        yield

    monkeypatch.setattr(local_demo, "runner_guard", blocked_guard)
    assert local_demo.main(["live"]) == 1
    assert "another FraudLens local stack" in capsys.readouterr().err


def test_require_tools_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_demo.shutil, "which", lambda _tool: None)
    with pytest.raises(RuntimeError, match="missing required tools"):
        local_demo._require_tools("docker")


def test_build_rag_index_runs_the_ingest_script(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(local_demo.subprocess, "run", lambda cmd, **_kw: calls.append(cmd))
    local_demo._build_rag_index({"X": "1"})
    assert calls == [["uv", "run", "python", "scripts/ingest_rag.py"]]


@pytest.mark.parametrize(
    ("function_name", "expected"),
    [
        (
            "_fetch_ibm_demo_data",
            ["uv", "run", "python", "scripts/fetch_dataset.py", "--source", "ibm-aml"],
        ),
        ("_ingest_ibm_demo_data", ["uv", "run", "python", "scripts/ingest_aml_demo.py"]),
        (
            "_score_ibm_demo_data",
            ["uv", "run", "python", "-m", "fraudlens_backend.jobs.runner"],
        ),
    ],
)
def test_ibm_bootstrap_helpers_run_canonical_commands(
    monkeypatch: pytest.MonkeyPatch, function_name: str, expected: list[str]
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    def run(cmd: list[str], **kwargs: object) -> None:
        calls.append((cmd, kwargs["env"]))

    monkeypatch.setattr(local_demo.subprocess, "run", run)
    env = {"SAFE": "value"}
    getattr(local_demo, function_name)(env)
    assert calls == [(expected, env)]


def test_provision_live_demo_auth_runs_with_the_live_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    def run(cmd: list[str], **kwargs: object) -> None:
        calls.append((cmd, kwargs["env"]))

    monkeypatch.setattr(local_demo.subprocess, "run", run)
    env = {"SAFE": "value"}
    local_demo._provision_live_demo_auth(env)
    assert calls == [
        (["uv", "run", "python", "scripts/provision_demo_auth.py"], env),
    ]


def test_build_rag_index_fails_hard_when_script_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    monkeypatch.setattr(local_demo, "REPO_ROOT", tmp_path)  # no scripts/ingest_rag.py here
    ran: list[object] = []
    monkeypatch.setattr(local_demo.subprocess, "run", lambda *a, **k: ran.append(a))
    with pytest.raises(RuntimeError, match="RAG ingest script is missing"):
        local_demo._build_rag_index({})
    assert ran == []


def test_migrate_and_seed_fails_hard_when_alembic_config_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    monkeypatch.setattr(local_demo, "REPO_ROOT", tmp_path)  # no alembic.ini here
    ran: list[object] = []
    monkeypatch.setattr(local_demo.subprocess, "run", lambda *a, **k: ran.append(a))
    with pytest.raises(RuntimeError, match="Alembic config is missing"):
        local_demo._migrate_and_seed({})
    assert ran == []


def test_clear_local_caches_removes_files_and_directories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache_dir = tmp_path / "cache-dir"
    cache_file = tmp_path / "cache-file"
    cache_dir.mkdir()
    cache_file.write_text("cache", encoding="utf-8")
    monkeypatch.setattr(local_demo, "_LOCAL_CACHE_PATHS", (cache_dir, cache_file))
    local_demo._clear_local_caches()
    assert not cache_dir.exists()
    assert not cache_file.exists()


def test_clear_local_caches_preserves_downloaded_ibm_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    generated = tmp_path / "generated"
    ibm_data = tmp_path / "aml_data" / "HI-Small_Trans.csv"
    generated.mkdir()
    ibm_data.parent.mkdir()
    ibm_data.write_text("public-data", encoding="utf-8")
    monkeypatch.setattr(local_demo, "_LOCAL_CACHE_PATHS", (generated,))
    local_demo._clear_local_caches()
    assert not generated.exists()
    assert ibm_data.read_text(encoding="utf-8") == "public-data"


def test_rebuild_resets_caches_ports_then_boots(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(local_demo, "_require_tools", lambda *tools: calls.append(f"tools:{tools}"))
    monkeypatch.setattr(
        local_demo, "_compose_down", lambda *, remove_volumes: calls.append("compose-down")
    )
    monkeypatch.setattr(local_demo, "_clear_local_caches", lambda: calls.append("clear-caches"))
    monkeypatch.setattr(
        local_demo, "_free_fraudlens_ports", lambda ports, **_kw: calls.append("ports")
    )
    monkeypatch.setattr(
        local_demo, "_assign_available_default_ports", lambda names: calls.append("auto-ports")
    )
    monkeypatch.setattr(local_demo, "up", lambda: calls.append("up") or 0)
    assert local_demo.rebuild() == 0
    assert calls == [
        "tools:('docker', 'uv', 'npm')",
        "compose-down",
        "clear-caches",
        "ports",
        "auto-ports",
        "ports",
        "up",
    ]


class _FakeProc:
    """Minimal stand-in for subprocess.Popen exposing poll()/returncode for the smoke gate."""

    def __init__(self, exit_code: int | None) -> None:
        self.returncode = exit_code
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        """Record a graceful stop requested by the orchestrator."""
        self.terminated = True

    def wait(self, *, timeout: int) -> int:
        """Return immediately like an already-cooperative child process."""
        del timeout
        return self.returncode or 0


def test_up_exposes_kaggle_token_only_to_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, bool]] = []
    env = {
        "BACKEND_PORT": "18000",
        "FRONTEND_PORT": "5173",
        "KAGGLE_API_TOKEN": "runtime-only",
    }

    def record(name: str):
        def inner(child_env: dict[str, str]) -> None:
            events.append((name, "KAGGLE_API_TOKEN" in child_env))

        return inner

    children: list[_FakeProc] = []

    def popen(*_args: object, **kwargs: object) -> _FakeProc:
        events.append(("server", "KAGGLE_API_TOKEN" in kwargs["env"]))
        child = _FakeProc(None)
        children.append(child)
        return child

    monkeypatch.setattr(local_demo, "_require_tools", lambda *_tools: None)
    monkeypatch.setattr(local_demo, "_assign_available_default_ports", lambda _names: None)
    monkeypatch.setattr(local_demo, "demo_environment", lambda: dict(env))
    monkeypatch.setattr(local_demo, "_fetch_ibm_demo_data", record("fetch"))
    monkeypatch.setattr(local_demo, "_start_postgres", record("postgres"))
    monkeypatch.setattr(local_demo, "_migrate_and_seed", record("seed"))
    monkeypatch.setattr(local_demo, "_activate_trained_model", record("activate"))
    monkeypatch.setattr(local_demo, "_ingest_ibm_demo_data", record("ingest"))
    monkeypatch.setattr(local_demo, "_build_rag_index", record("rag"))
    monkeypatch.setattr(local_demo, "_score_ibm_demo_data", record("score"))
    monkeypatch.setattr(local_demo.subprocess, "Popen", popen)
    monkeypatch.setattr(local_demo, "_wait_for_http", lambda _url: False)

    assert local_demo.up() == 1
    assert events == [
        ("fetch", True),
        ("postgres", False),
        ("seed", False),
        ("activate", False),  # model promotion runs after the seed, before ingest - keyless
        ("ingest", False),
        ("rag", False),
        ("score", False),
        ("server", False),
        ("server", False),
    ]
    assert all(child.terminated for child in children)


def test_await_backend_ready_passes_when_healthz_and_readyz_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(local_demo, "_http_ok", lambda _url: True)
    assert local_demo._await_backend_ready("http://localhost:8000", _FakeProc(None)) is True


def test_await_backend_ready_bails_fast_when_backend_exits(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(local_demo, "_http_ok", lambda _url: False)
    monkeypatch.setattr(local_demo.time, "sleep", lambda _s: None)
    # Process already dead (code 1) -> fail immediately, no waiting for the full timeout.
    assert local_demo._await_backend_ready("http://localhost:8000", _FakeProc(1)) is False
    assert "backend exited (code 1)" in capsys.readouterr().err


def test_await_backend_ready_times_out_when_never_ready(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(local_demo, "_http_ok", lambda _url: False)
    monkeypatch.setattr(local_demo.time, "sleep", lambda _s: None)
    # Live process (poll None) that never serves /healthz -> times out by name.
    assert (
        local_demo._await_backend_ready("http://localhost:8000", _FakeProc(None), timeout=0.0)
        is False
    )
    assert "timed out waiting for /healthz" in capsys.readouterr().err


def test_bootstrap_portfolio_demo_runs_the_canonical_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    def run(cmd: list[str], **kwargs: object) -> None:
        calls.append((cmd, kwargs["env"]))

    monkeypatch.setattr(local_demo.subprocess, "run", run)
    env = {"SAFE": "value"}
    local_demo._bootstrap_portfolio_demo(env)
    assert calls == [(["uv", "run", "python", "scripts/bootstrap_portfolio_demo.py"], env)]


def test_bootstrap_portfolio_demo_fails_hard_when_script_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(local_demo, "REPO_ROOT", tmp_path)  # no bootstrap script here
    ran: list[object] = []
    monkeypatch.setattr(local_demo.subprocess, "run", lambda *a, **k: ran.append(a))
    with pytest.raises(RuntimeError, match="portfolio demo bootstrap script is missing"):
        local_demo._bootstrap_portfolio_demo({})
    assert ran == []


def test_portfolio_story_environment_pins_the_configured_provider_modes() -> None:
    execution = load_portfolio_demo_config().execution
    env = local_demo._portfolio_story_environment({"FRAUDLENS_LLM_MODE": "live", "KEEP": "yes"})
    # The story's own modes win over whatever live mode was inherited, and nothing else moves.
    assert env["FRAUDLENS_LLM_MODE"] == execution.llm_mode
    assert env["FRAUDLENS_RAG_EMBEDDING_MODE"] == execution.rag_embedding_mode
    assert env["KEEP"] == "yes"


def test_portfolio_story_environment_opens_the_demo_gate() -> None:
    """Without this the projection 404s and the picker cannot auto-fill: live mode has no bypass."""
    env = local_demo._portfolio_story_environment({"FRAUDLENS_AUTH_DEV_BYPASS": "false"})
    assert env["FRAUDLENS_PORTFOLIO_DEMO_ENABLED"] == "true"
    # Opening the demo gate must not also open the tokenless bypass; live mode keeps real auth.
    assert env["FRAUDLENS_AUTH_DEV_BYPASS"] == "false"


def test_live_environment_leaves_the_demo_gate_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """`make run-live` is not the portfolio path, so it must not open the demo surface."""
    for name in ("SUPABASE_URL", "DATABASE_URL", "OPENROUTER_API_KEY"):
        monkeypatch.setenv(name, "set-for-this-test")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "set-for-this-test")
    monkeypatch.setenv("VITE_SUPABASE_ANON_KEY", "set-for-this-test")
    monkeypatch.delenv("FRAUDLENS_PORTFOLIO_DEMO_ENABLED", raising=False)
    assert "FRAUDLENS_PORTFOLIO_DEMO_ENABLED" not in local_demo.live_environment()


def test_live_demo_seeds_provisions_and_bootstraps_before_serving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    env = {"BACKEND_PORT": "18000", "FRONTEND_PORT": "15173"}

    def record(name: str):
        def inner(_child_env: dict[str, str]) -> None:
            events.append(name)

        return inner

    monkeypatch.setattr(local_demo, "_require_tools", lambda *_tools: None)
    monkeypatch.setattr(local_demo, "_assign_available_default_ports", lambda _names: None)
    monkeypatch.setattr(local_demo, "live_environment", lambda: dict(env))
    monkeypatch.setattr(local_demo, "_portfolio_story_environment", lambda child_env: child_env)
    monkeypatch.setattr(local_demo, "_migrate_and_seed", record("seed"))
    monkeypatch.setattr(local_demo, "_provision_live_demo_auth", record("provision"))
    monkeypatch.setattr(local_demo, "_build_rag_index", record("rag"))
    monkeypatch.setattr(local_demo, "_bootstrap_portfolio_demo", record("bootstrap"))
    monkeypatch.setattr(
        local_demo, "_serve", lambda _child_env, *, banner: events.append(banner) or 0
    )

    assert local_demo.live_demo() == 0
    assert events == [
        "seed",
        "provision",
        "rag",
        "bootstrap",
        "FraudLens portfolio demo is up",
    ]


def test_live_demo_hands_every_child_the_story_execution_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, str]] = []
    story_env = {"BACKEND_PORT": "18000", "FRONTEND_PORT": "15173", "FRAUDLENS_LLM_MODE": "mock"}

    monkeypatch.setattr(local_demo, "_require_tools", lambda *_tools: None)
    monkeypatch.setattr(local_demo, "_assign_available_default_ports", lambda _names: None)
    monkeypatch.setattr(local_demo, "live_environment", lambda: {"FRAUDLENS_LLM_MODE": "live"})
    monkeypatch.setattr(local_demo, "_portfolio_story_environment", lambda _env: dict(story_env))

    def observe(child_env: dict[str, str]) -> None:
        seen.append(child_env)

    steps = (
        "_migrate_and_seed",
        "_provision_live_demo_auth",
        "_build_rag_index",
        "_bootstrap_portfolio_demo",
    )
    for step in steps:
        monkeypatch.setattr(local_demo, step, observe)
    monkeypatch.setattr(
        local_demo, "_serve", lambda child_env, *, banner: seen.append(child_env) or 0
    )

    assert local_demo.live_demo() == 0
    # Index build, bootstrap, and the servers must agree, or the index is unusable at query time.
    assert [child["FRAUDLENS_LLM_MODE"] for child in seen] == ["mock"] * len(seen)


def test_live_stays_non_mutating(monkeypatch: pytest.MonkeyPatch) -> None:
    """`make run-live` provisions identities only — it must never seed or write story rows."""
    events: list[str] = []
    monkeypatch.setattr(local_demo, "_require_tools", lambda *_tools: None)
    monkeypatch.setattr(local_demo, "_assign_available_default_ports", lambda _names: None)
    monkeypatch.setattr(
        local_demo, "live_environment", lambda: {"BACKEND_PORT": "1", "FRONTEND_PORT": "2"}
    )
    monkeypatch.setattr(
        local_demo, "_provision_live_demo_auth", lambda _env: events.append("provision")
    )
    for mutating in ("_migrate_and_seed", "_build_rag_index", "_bootstrap_portfolio_demo"):
        monkeypatch.setattr(local_demo, mutating, lambda _env, name=mutating: events.append(name))
    monkeypatch.setattr(local_demo, "_serve", lambda _env, *, banner: events.append("serve") or 0)

    assert local_demo.live() == 0
    assert events == ["provision", "serve"]
