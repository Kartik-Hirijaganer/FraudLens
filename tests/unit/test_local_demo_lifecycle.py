"""Local-demo boot, readiness, portfolio-story, and live lifecycle tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from local_demo_fakes import (
    _FakeProc,
)

import lib.demo_dataset_steps as dataset_steps
import local_demo
from fraudlens_backend.portfolio_demo import load_portfolio_demo_config
from lib import demo_processes


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
    monkeypatch.setattr(demo_processes, "http_ok", lambda _url: True)
    assert local_demo._await_backend_ready("http://localhost:8000", _FakeProc(None)) is True


def test_await_backend_ready_bails_fast_when_backend_exits(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(demo_processes, "http_ok", lambda _url: False)
    monkeypatch.setattr(demo_processes.time, "sleep", lambda _s: None)
    # Process already dead (code 1) -> fail immediately, no waiting for the full timeout.
    assert local_demo._await_backend_ready("http://localhost:8000", _FakeProc(1)) is False
    assert "backend exited (code 1)" in capsys.readouterr().err


def test_await_backend_ready_times_out_when_never_ready(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(demo_processes, "http_ok", lambda _url: False)
    monkeypatch.setattr(demo_processes.time, "sleep", lambda _s: None)
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

    monkeypatch.setattr(dataset_steps.subprocess, "run", run)
    env = {"SAFE": "value"}
    local_demo._bootstrap_portfolio_demo(env)
    assert calls == [(["uv", "run", "python", "scripts/bootstrap_portfolio_demo.py"], env)]


def test_bootstrap_portfolio_demo_fails_hard_when_script_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(dataset_steps, "REPO_ROOT", tmp_path)  # no bootstrap script here
    ran: list[object] = []
    monkeypatch.setattr(dataset_steps.subprocess, "run", lambda *a, **k: ran.append(a))
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


def test_serve_reports_urls_and_stops_children(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    children = [_FakeProc(None), _FakeProc(None)]
    monkeypatch.setattr(local_demo.subprocess, "Popen", lambda *_args, **_kwargs: children.pop(0))
    monkeypatch.setattr(local_demo, "_wait_for_http", lambda _url: True)
    monkeypatch.setattr(
        local_demo.signal, "pause", lambda: (_ for _ in ()).throw(KeyboardInterrupt)
    )
    retained = children.copy()

    assert (
        local_demo._serve({"BACKEND_PORT": "18000", "FRONTEND_PORT": "15173"}, banner="Test stack")
        == 0
    )
    assert "Test stack" in capsys.readouterr().out
    assert all(child.terminated for child in retained)


def test_down_and_reset_delegate_to_bounded_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[object] = []
    monkeypatch.setattr(local_demo, "_require_tools", lambda *tools: events.append(tools))
    monkeypatch.setattr(
        local_demo,
        "_compose_down",
        lambda *, remove_volumes: events.append(("compose", remove_volumes)),
    )
    monkeypatch.setattr(local_demo, "_clear_local_caches", lambda: events.append("caches"))
    monkeypatch.setattr(local_demo, "_remove_path", events.append)

    assert local_demo.down() == 0
    assert local_demo.reset() == 0
    assert events == [
        ("docker",),
        ("compose", False),
        ("docker",),
        ("compose", True),
        "caches",
        local_demo.LOCAL_STATE_DIR,
    ]


def test_smoke_runs_readiness_and_always_tears_down(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _FakeProc(None)
    events: list[str] = []
    monkeypatch.setattr(local_demo, "_require_tools", lambda *_tools: None)
    monkeypatch.setattr(local_demo, "_assign_available_default_ports", lambda _names: None)
    monkeypatch.setattr(
        local_demo,
        "demo_environment",
        lambda: {"BACKEND_PORT": "18000", "FRONTEND_PORT": "15173"},
    )
    monkeypatch.setattr(local_demo, "_start_postgres", lambda _env: events.append("postgres"))
    monkeypatch.setattr(local_demo, "_migrate_and_seed", lambda _env: events.append("seed"))
    monkeypatch.setattr(local_demo, "_build_rag_index", lambda _env: events.append("rag"))
    monkeypatch.setattr(local_demo, "_await_backend_ready", lambda _url, _proc: True)
    monkeypatch.setattr(local_demo.subprocess, "Popen", lambda *_args, **_kwargs: backend)
    monkeypatch.setattr(
        local_demo.subprocess, "run", lambda *_args, **_kwargs: events.append("compose-down")
    )

    assert local_demo.smoke() == 0
    assert events == ["postgres", "seed", "rag", "compose-down"]
    assert backend.terminated is True
