"""Behavioral tests for health and durable-investigation Kubernetes load generation."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any, ClassVar

import pytest
from pydantic import HttpUrl, SecretStr

from lib.k8s_demo import load as load_module
from lib.k8s_demo.config import LoadConfig
from lib.k8s_demo.load import SUMMARY_PREFIX, run_load, summary_from_logs


class _HealthHandler(BaseHTTPRequestHandler):
    authorization_headers: ClassVar[list[str | None]] = []

    def do_GET(self) -> None:
        self.authorization_headers.append(self.headers.get("Authorization"))
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args: object) -> None:
        del args


def _config(*, mode: str = "healthz") -> LoadConfig:
    return LoadConfig(
        target_url=HttpUrl("http://service:8000/healthz"),
        mode=mode,
        concurrency=2,
        duration_seconds=1,
        reconnect_every=5,
        cases=2,
    )


def test_health_load_hits_local_server_without_failures() -> None:
    _HealthHandler.authorization_headers.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = _config().model_copy(
            update={"target_url": HttpUrl(f"http://127.0.0.1:{server.server_port}/healthz")}
        )
        summary = run_load(config)
    finally:
        server.shutdown()
        thread.join()
    assert summary.requests > 0
    assert summary.succeeded == summary.requests
    assert summary.failed == 0
    assert summary.latency_p95_ms >= summary.latency_p50_ms
    assert set(_HealthHandler.authorization_headers) == {None}


def test_authenticated_load_attaches_bearer_and_fails_closed_without_it() -> None:
    _HealthHandler.authorization_headers.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = _config(mode="authenticated").model_copy(
            update={
                "target_url": HttpUrl(f"http://127.0.0.1:{server.server_port}/api/v1/me"),
                "auth_required": True,
                "auth_token": SecretStr("synthetic-auth-token"),
            }
        )
        summary = run_load(config)
    finally:
        server.shutdown()
        thread.join()
    assert summary.failed == 0
    assert set(_HealthHandler.authorization_headers) == {"Bearer synthetic-auth-token"}
    with pytest.raises(ValueError, match="requires an injected bearer token"):
        run_load(config.model_copy(update={"auth_token": None}))


def test_investigation_load_submits_and_polls_every_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    poll_count = 0

    def request(
        _parts: Any,
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any], float]:
        nonlocal poll_count
        assert headers is not None
        assert headers["Authorization"] == "Bearer synthetic-auth-token"
        if path.endswith("/transactions/batch"):
            assert body is not None
            return (
                200,
                {
                    "accepted": 2,
                    "transactions": [
                        {"transactionId": "transaction-1"},
                        {"transactionId": "transaction-2"},
                    ],
                },
                0.01,
            )
        if method == "POST":
            assert body is not None
            return 202, {"runId": f"run-{body['transactionId']}"}, 0.02
        poll_count += 1
        return 200, {"status": "completed", "attempt": 2}, 0.03

    monkeypatch.setattr(load_module, "_request_json", request)
    config = _config(mode="investigations").model_copy(
        update={"auth_required": True, "auth_token": SecretStr("synthetic-auth-token")}
    )
    summary = run_load(config)
    assert summary.runs_submitted == 2
    assert summary.runs_completed == 2
    assert summary.runs_failed == 0
    assert summary.max_run_attempts == 2
    assert poll_count == 2
    assert "K8S_DEMO_SUBMITTED=2" in capsys.readouterr().out


def test_investigation_load_reports_batch_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        load_module,
        "_request_json",
        lambda *_args, **_kwargs: (422, {"accepted": 0}, 0.01),
    )
    summary = run_load(_config(mode="investigations"))
    assert summary.requests == 1
    assert summary.failed == 1
    assert summary.runs_submitted == 0


def test_summary_log_parser_uses_last_marker_and_rejects_missing() -> None:
    first = run_load.__module__
    document = {
        "mode": "healthz",
        "requests": 1,
        "succeeded": 1,
        "failed": 0,
        "duration_seconds": 1,
        "latency_p50_ms": 1,
        "latency_p95_ms": 1,
        "runs_submitted": 0,
        "runs_completed": 0,
        "runs_failed": 0,
        "max_run_attempts": 0,
    }
    summary = summary_from_logs(f"noise\n{SUMMARY_PREFIX}{json.dumps(document)}\n")
    assert summary.requests == 1
    assert first == "lib.k8s_demo.load"
    with pytest.raises(ValueError, match="no final summary"):
        summary_from_logs("noise only")
