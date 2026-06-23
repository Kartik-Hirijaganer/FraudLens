"""Unit tests for the local-demo orchestrator helpers + command dispatch (skeleton)."""

from __future__ import annotations

from pathlib import Path

import pytest

import local_demo


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
    assert env["FRAUDLENS_STORAGE_BACKEND"] == "local"
    assert env["FRAUDLENS_QUEUE_BACKEND"] == "local"
    assert env["FRAUDLENS_LLM_MODE"] == "mock"
    assert env["VITE_API_BASE_URL"] == "http://localhost:8000"
    assert env["FRAUDLENS_CORS_ALLOW_ORIGINS"] == '["http://localhost:5173"]'
    assert env["DATABASE_URL"].startswith("postgresql+asyncpg://")


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
    for name in ("up", "down", "rebuild", "reset", "run", "smoke"):
        monkeypatch.setitem(local_demo._COMMANDS, name, lambda n=name: calls.append(n) or 0)
    assert local_demo.main(["down"]) == 0
    assert local_demo.main(["smoke"]) == 0
    assert local_demo.main(["run"]) == 0
    assert calls == ["down", "smoke", "run"]


def test_require_tools_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_demo.shutil, "which", lambda _tool: None)
    with pytest.raises(RuntimeError, match="missing required tools"):
        local_demo._require_tools("docker")


def test_maybe_build_rag_index_runs_the_ingest_script(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(local_demo.subprocess, "run", lambda cmd, **_kw: calls.append(cmd))
    local_demo._maybe_build_rag_index({"X": "1"})
    assert calls == [["uv", "run", "python", "scripts/ingest_rag.py"]]


def test_maybe_build_rag_index_skips_when_script_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(local_demo, "REPO_ROOT", tmp_path)  # no scripts/ingest_rag.py here
    ran: list[object] = []
    monkeypatch.setattr(local_demo.subprocess, "run", lambda *a, **k: ran.append(a))
    local_demo._maybe_build_rag_index({})
    assert ran == []
    assert "rag index: skipped" in capsys.readouterr().out


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

    def poll(self) -> int | None:
        return self.returncode


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
