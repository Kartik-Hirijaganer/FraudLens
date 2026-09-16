"""Summary: Standard-library HTTP load generator for HPA and durable-worker demonstrations.

Key classes:
- LoadSummary: PHI-free counts, timings, and durable run outcomes emitted by the load Job.

Key functions:
- run_load: dispatch probe, authenticated API, or durable-investigation load from validated config.
- summary_from_logs: extract the machine-readable final summary from Kubernetes Job logs.

Notes:
- Investigation inputs are deterministic synthetic records. Raw account values are never logged.
"""

from __future__ import annotations

import concurrent.futures
import http.client
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from lib.k8s_demo.config import LoadConfig

SUMMARY_PREFIX = "K8S_DEMO_SUMMARY="
SUBMITTED_PREFIX = "K8S_DEMO_SUBMITTED="
_TERMINAL_STATUSES = frozenset({"completed", "failed"})
_HTTP_OK = 200
_HTTP_ACCEPTED = 202


def _authorization_headers(config: LoadConfig) -> dict[str, str]:
    """Return the runtime bearer header, failing closed when AKS auth is required."""
    if config.auth_token is None:
        if config.auth_required:
            raise ValueError("authenticated load requires an injected bearer token")
        return {}
    return {"Authorization": f"Bearer {config.auth_token.get_secret_value()}"}


class LoadSummary(BaseModel):
    """Safe final outcome from a probe-load or durable-investigation Job."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: str = Field(..., description="Executed load mode.")
    requests: int = Field(..., ge=0, description="Total HTTP requests attempted.")
    succeeded: int = Field(..., ge=0, description="Successful HTTP requests.")
    failed: int = Field(..., ge=0, description="Failed HTTP requests.")
    duration_seconds: float = Field(..., ge=0, description="Wall duration.")
    latency_p50_ms: float = Field(..., ge=0, description="Median request latency.")
    latency_p95_ms: float = Field(..., ge=0, description="95th percentile request latency.")
    runs_submitted: int = Field(default=0, ge=0, description="Durable runs accepted.")
    runs_completed: int = Field(default=0, ge=0, description="Runs ending completed.")
    runs_failed: int = Field(default=0, ge=0, description="Runs ending failed.")
    max_run_attempts: int = Field(default=0, ge=0, description="Maximum run claim attempt.")


def _percentile(values: list[float], quantile: float) -> float:
    """Return a nearest-rank percentile in milliseconds for a non-empty timing set."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * quantile)))
    return round(ordered[index] * 1000, 3)


def _connection(parts: Any) -> http.client.HTTPConnection:
    """Open an HTTP(S) connection from a validated URL split result."""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    connection_type = (
        http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    )
    return connection_type(parts.hostname, port, timeout=10)


def _health_worker(config: LoadConfig, deadline: float) -> tuple[int, int, list[float]]:
    """Drive one persistent HTTP connection until the shared deadline."""
    parts = urlsplit(str(config.target_url))
    path = parts.path or "/healthz"
    successes = failures = sent = 0
    latencies: list[float] = []
    connection: http.client.HTTPConnection | None = None
    while time.monotonic() < deadline:
        if connection is None or sent % config.reconnect_every == 0:
            if connection is not None:
                connection.close()
            connection = _connection(parts)
        started = time.monotonic()
        try:
            connection.request("GET", path, headers=_authorization_headers(config))
            response = connection.getresponse()
            response.read()
            if response.status == _HTTP_OK:
                successes += 1
            else:
                failures += 1
        except OSError:
            failures += 1
            connection = None
        latencies.append(time.monotonic() - started)
        sent += 1
    if connection is not None:
        connection.close()
    return successes, failures, latencies


def _request_json(
    parts: Any,
    method: str,
    path: str,
    *,
    body: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any], float]:
    """Make one bounded JSON request and return status, decoded mapping, and latency."""
    connection = _connection(parts)
    encoded = json.dumps(body).encode() if body is not None else None
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    started = time.monotonic()
    try:
        connection.request(method, path, body=encoded, headers=request_headers)
        response = connection.getresponse()
        raw = response.read()
        decoded = json.loads(raw) if raw else {}
        return response.status, decoded, time.monotonic() - started
    finally:
        connection.close()


def _synthetic_transaction(index: int) -> dict[str, object]:
    """Build a deterministic, non-PHI transaction old enough to pass future-date validation."""
    occurred_at = datetime.now(UTC) - timedelta(days=30, minutes=index)
    return {
        "externalId": f"K8S-{uuid.uuid4().hex}",
        "amount": str(1000 + index * 17),
        "currency": "USD",
        "occurredAt": occurred_at.isoformat(),
        "originAccount": f"synthetic-origin-{index}",
        "destAccount": f"synthetic-destination-{index}",
        "channel": "wire",
        "country": "US",
        "features": {"cashDepositCount7d": index % 5, "velocity1h": index % 3},
    }


def _investigation_load(config: LoadConfig) -> LoadSummary:
    """Ingest synthetic transactions, submit durable runs, and poll every run terminal."""
    started = time.monotonic()
    parts = urlsplit(str(config.target_url))
    transactions = [_synthetic_transaction(index) for index in range(config.cases)]
    auth_headers = _authorization_headers(config)
    status, response, latency = _request_json(
        parts,
        "POST",
        "/api/v1/transactions/batch",
        body={"transactions": transactions},
        headers=auth_headers,
    )
    latencies = [latency]
    if status != _HTTP_OK or response.get("accepted") != config.cases:
        return LoadSummary(
            mode=config.mode,
            requests=1,
            succeeded=0,
            failed=1,
            duration_seconds=time.monotonic() - started,
            latency_p50_ms=_percentile(latencies, 0.5),
            latency_p95_ms=_percentile(latencies, 0.95),
        )
    transaction_ids = [item["transactionId"] for item in response.get("transactions", [])]

    def submit(item: tuple[int, str]) -> tuple[int, str | None, float]:
        index, transaction_id = item
        request_status, document, elapsed = _request_json(
            parts,
            "POST",
            "/api/v1/investigations",
            body={"transactionId": transaction_id},
            headers={
                **auth_headers,
                "Idempotency-Key": f"k8s-demo-{index}-{transaction_id}",
            },
        )
        return request_status, document.get("runId"), elapsed

    with concurrent.futures.ThreadPoolExecutor(max_workers=config.concurrency) as executor:
        submitted = list(executor.map(submit, enumerate(transaction_ids)))
    latencies.extend(item[2] for item in submitted)
    run_ids = [item[1] for item in submitted if item[0] == _HTTP_ACCEPTED and item[1]]
    print(f"{SUBMITTED_PREFIX}{len(run_ids)}", flush=True)
    deadline = time.monotonic() + max(config.duration_seconds, 300)
    terminal: dict[str, dict[str, Any]] = {}
    while len(terminal) < len(run_ids) and time.monotonic() < deadline:
        pending = [run_id for run_id in run_ids if run_id not in terminal]

        def poll(run_id: str) -> tuple[str, int, dict[str, Any], float]:
            poll_status, document, elapsed = _request_json(
                parts,
                "GET",
                f"/api/v1/investigations/{run_id}",
                headers=auth_headers,
            )
            return run_id, poll_status, document, elapsed

        with concurrent.futures.ThreadPoolExecutor(max_workers=config.concurrency) as executor:
            observations = list(executor.map(poll, pending))
        for run_id, poll_status, document, elapsed in observations:
            latencies.append(elapsed)
            if poll_status == _HTTP_OK and document.get("status") in _TERMINAL_STATUSES:
                terminal[run_id] = document
        if len(terminal) < len(run_ids):
            time.sleep(0.5)
    completed = sum(item.get("status") == "completed" for item in terminal.values())
    failed_runs = sum(item.get("status") == "failed" for item in terminal.values())
    return LoadSummary(
        mode=config.mode,
        requests=len(latencies),
        succeeded=1 + len(run_ids) + len(terminal),
        failed=(len(submitted) - len(run_ids)) + (len(run_ids) - len(terminal)),
        duration_seconds=round(time.monotonic() - started, 3),
        latency_p50_ms=_percentile(latencies, 0.5),
        latency_p95_ms=_percentile(latencies, 0.95),
        runs_submitted=len(run_ids),
        runs_completed=completed,
        runs_failed=failed_runs,
        max_run_attempts=max(
            (int(item.get("attempt", 0)) for item in terminal.values()), default=0
        ),
    )


def run_load(config: LoadConfig) -> LoadSummary:
    """Execute the configured load mode and return a PHI-free summary."""
    if config.mode == "investigations":
        return _investigation_load(config)
    started = time.monotonic()
    deadline = started + config.duration_seconds
    with concurrent.futures.ThreadPoolExecutor(max_workers=config.concurrency) as executor:
        results = list(
            executor.map(lambda _index: _health_worker(config, deadline), range(config.concurrency))
        )
    successes = sum(item[0] for item in results)
    failures = sum(item[1] for item in results)
    latencies = [latency for item in results for latency in item[2]]
    return LoadSummary(
        mode=config.mode,
        requests=successes + failures,
        succeeded=successes,
        failed=failures,
        duration_seconds=round(time.monotonic() - started, 3),
        latency_p50_ms=_percentile(latencies, 0.5),
        latency_p95_ms=_percentile(latencies, 0.95),
    )


def summary_from_logs(logs: str) -> LoadSummary:
    """Parse the last prefixed summary line from Kubernetes Job logs."""
    candidates = [
        line.removeprefix(SUMMARY_PREFIX)
        for line in logs.splitlines()
        if line.startswith(SUMMARY_PREFIX)
    ]
    if not candidates:
        raise ValueError("load Job logs contain no final summary")
    return LoadSummary.model_validate_json(candidates[-1])
