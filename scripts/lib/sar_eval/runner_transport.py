"""Summary: HTTP transport, idempotent ingestion, and terminal polling for SAR evaluation.

Key classes:
- (none)

Key functions:
- atomic_write: persist an artifact through an atomic replacement.
- response_body: validate one JSON object response.
- matches_visible_transaction:
- ingest: idempotently ingest a synthetic scenario.
- poll: wait for one durable investigation terminal state.

Notes:
- Errors contain status and protocol context only, never transaction payloads.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

from lib.sar_eval.runner_contracts import _TerminalInvestigationError
from lib.sar_eval.scenarios import SarEvalScenario, SyntheticTransaction
from lib.study.artifacts import atomic_write_model

_TERMINAL = frozenset({"completed", "failed"})
_CREATED = 201
_CONFLICT = 409


def atomic_write(path: Path, value: BaseModel) -> None:
    """Preserve the SAR-eval seam while using the shared study writer."""
    atomic_write_model(path, value)


def response_body(response: httpx.Response, expected: int) -> dict[str, Any]:
    if response.status_code != expected:
        raise RuntimeError(f"API request failed with status {response.status_code}")
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError("API response must be an object")
    return value


def matches_visible_transaction(row: Mapping[str, Any], transaction: SyntheticTransaction) -> bool:
    try:
        occurred_at = datetime.fromisoformat(str(row["occurredAt"]).replace("Z", "+00:00"))
        amount = Decimal(str(row["amount"]))
    except (KeyError, ArithmeticError, TypeError, ValueError):
        return False
    expected_time = transaction.occurred_at
    return (
        row.get("externalId") == transaction.external_id
        and amount == transaction.amount
        and row.get("currency") == transaction.currency
        and occurred_at == expected_time
        and row.get("channel") == transaction.channel
        and row.get("country") == transaction.country
    )


def ingest(client: httpx.Client, scenario: SarEvalScenario) -> str:
    subject_id: str | None = None
    for transaction in scenario.transactions:
        response = client.post(
            "/api/v1/transactions",
            json=transaction.model_dump(mode="json", by_alias=True),
        )
        if response.status_code == _CREATED:
            body = response_body(response, _CREATED)
        elif response.status_code == _CONFLICT:
            listing = response_body(
                client.get(
                    "/api/v1/transactions",
                    params={"search": transaction.external_id, "limit": 100},
                ),
                200,
            )
            rows = listing.get("transactions")
            matches = (
                [
                    row
                    for row in rows
                    if isinstance(row, dict) and row.get("externalId") == transaction.external_id
                ]
                if isinstance(rows, list)
                else []
            )
            if len(matches) != 1:
                raise RuntimeError("duplicate synthetic transaction could not be resolved exactly")
            body = matches[0]
            if not matches_visible_transaction(body, transaction):
                raise RuntimeError(
                    "duplicate synthetic transaction differs from the current scenario"
                )
        else:
            raise RuntimeError(f"transaction ingest failed with status {response.status_code}")
        if transaction.external_id == scenario.subject_external_id:
            subject_id = str(body["transactionId"])
    if subject_id is None:
        raise RuntimeError("scenario subject transaction was not ingested")
    return subject_id


def poll(  # noqa: PLR0913 -- injectable time functions keep polling deterministic.
    client: httpx.Client,
    run_id: str,
    *,
    timeout_s: float,
    poll_interval_s: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    deadline = clock() + timeout_s
    while clock() < deadline:
        try:
            snapshot = response_body(client.get(f"/api/v1/investigations/{run_id}"), 200)
        except httpx.TimeoutException:
            sleep(poll_interval_s)
            continue
        if snapshot.get("runId") not in (None, run_id):
            raise RuntimeError("investigation snapshot run id does not match the requested run")
        status = snapshot.get("status")
        if status in _TERMINAL:
            if status != "completed":
                raise _TerminalInvestigationError(
                    "investigation failed before producing an evaluation artifact"
                )
            return snapshot
        sleep(poll_interval_s)
    raise RuntimeError("investigation did not finish before the configured timeout")
