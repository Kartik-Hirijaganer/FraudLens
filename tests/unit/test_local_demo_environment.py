"""Local-demo environment, command, and reset behavior tests."""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

import lib.demo_dataset_steps as dataset_steps
import local_demo
from lib import demo_environment, demo_processes


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
    # setenv-then-delenv makes monkeypatch record the variable, so the assignment the call
    # below writes straight into os.environ is undone at teardown.
    monkeypatch.setenv("BACKEND_PORT", "8000")
    monkeypatch.delenv("BACKEND_PORT")
    monkeypatch.setattr(demo_environment, "_is_port_available", lambda port: port == "18000")
    local_demo._assign_available_default_ports(("BACKEND_PORT",))
    assert local_demo._env("BACKEND_PORT") == "18000"


def test_assign_available_default_ports_honors_explicit_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BACKEND_PORT", "19000")
    monkeypatch.setattr(demo_environment, "_is_port_available", lambda _port: False)
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
    monkeypatch.setattr(demo_processes.shutil, "which", lambda _tool: None)
    with pytest.raises(RuntimeError, match="missing required tools"):
        local_demo._require_tools("docker")


def test_build_rag_index_runs_the_ingest_script(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(dataset_steps.subprocess, "run", lambda cmd, **_kw: calls.append(cmd))
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

    monkeypatch.setattr(dataset_steps.subprocess, "run", run)
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
    monkeypatch.setattr(dataset_steps, "REPO_ROOT", tmp_path)  # no scripts/ingest_rag.py here
    ran: list[object] = []
    monkeypatch.setattr(dataset_steps.subprocess, "run", lambda *a, **k: ran.append(a))
    with pytest.raises(RuntimeError, match="RAG ingest script is missing"):
        local_demo._build_rag_index({})
    assert ran == []


def test_migrate_and_seed_fails_hard_when_alembic_config_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    monkeypatch.setattr(dataset_steps, "REPO_ROOT", tmp_path)  # no alembic.ini here
    ran: list[object] = []
    monkeypatch.setattr(dataset_steps.subprocess, "run", lambda *a, **k: ran.append(a))
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
    monkeypatch.setattr(demo_processes, "LOCAL_CACHE_PATHS", (cache_dir, cache_file))
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
    monkeypatch.setattr(demo_processes, "LOCAL_CACHE_PATHS", (generated,))
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
