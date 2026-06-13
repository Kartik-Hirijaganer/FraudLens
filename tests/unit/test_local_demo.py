"""Unit tests for the local-demo orchestrator helpers + command dispatch (skeleton)."""

from __future__ import annotations

import pytest

import local_demo


def test_local_database_url_uses_async_driver_and_defaults() -> None:
    url = local_demo.local_database_url()
    assert url == "postgresql+asyncpg://fraudlens:fraudlens@localhost:5432/fraudlens"


def test_local_database_url_honors_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "db.internal")
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    assert "@db.internal:6543/" in local_demo.local_database_url()


def test_demo_environment_selects_local_backends_and_mock_llm() -> None:
    env = local_demo.demo_environment()
    assert env["FRAUDLENS_ENVIRONMENT"] == "dev"
    assert env["FRAUDLENS_STORAGE_BACKEND"] == "local"
    assert env["FRAUDLENS_QUEUE_BACKEND"] == "local"
    assert env["FRAUDLENS_LLM_MODE"] == "mock"
    assert env["DATABASE_URL"].startswith("postgresql+asyncpg://")


def test_compose_command_targets_the_local_file() -> None:
    cmd = local_demo._compose("up", "-d")
    assert cmd[:3] == ["docker", "compose", "-f"]
    assert cmd[-2:] == ["up", "-d"]
    assert cmd[3].endswith("docker-compose.local.yml")


def test_base_url_is_built_from_host_and_port() -> None:
    assert local_demo._base_url("8000") == "http://localhost:8000"


def test_main_dispatches_to_the_named_command(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    for name in ("up", "down", "reset", "smoke"):
        monkeypatch.setitem(local_demo._COMMANDS, name, lambda n=name: calls.append(n) or 0)
    assert local_demo.main(["down"]) == 0
    assert local_demo.main(["smoke"]) == 0
    assert calls == ["down", "smoke"]


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
