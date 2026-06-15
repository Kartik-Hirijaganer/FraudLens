"""No-leak gate (plan §16 Phase 13, §8.4): errors and the MLOps audit trail never carry PHI,
secrets, raw input, or internals. The error envelope never echoes the rejected value or an
exception class/stack, and the inference/drift tables that persist per-run ML signals carry only
hashes + metrics — no account identifiers or other PHI columns (plan §9.2 / ADR-015)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from fraudlens_backend.db.models import Agency
from fraudlens_backend.db.models.mlops import DriftReport, ModelInferenceLog
from fraudlens_backend.demo import DEMO_AGENCY_ID

# Column-name fragments that would indicate raw PHI / direct identifiers leaking into a table.
_PHI_COLUMN_FRAGMENTS = ("account", "ssn", "email", "phone", "pan", "card", "address", "name")
_ENVELOPE_KEYS = {"code", "message", "details", "requestId"}


async def test_error_envelope_never_echoes_rejected_input(
    make_security_app: Callable[..., Any],
    aclient: Callable[[Any], httpx.AsyncClient],
    db_sessionmaker: async_sessionmaker[Any],
) -> None:
    async with db_sessionmaker() as session:
        session.add(Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo"))
        await session.commit()
    app = make_security_app(environment="dev", auth_dev_bypass=True)
    # A PHI-shaped, invalid amount: the validation error must name the FIELD, never echo the value.
    poison = "000-11-2222"
    body = {
        "externalId": "T1",
        "amount": poison,
        "currency": "usd",
        "occurredAt": "2026-01-01T00:00:00+00:00",
        "originAccount": "4111111111111111",
        "destAccount": "987654321",
        "channel": "wire",
        "country": "us",
    }
    async with aclient(app) as client:
        resp = await client.post("/api/v1/transactions", json=body)

    assert resp.status_code == 422
    text = resp.text
    assert poison not in text  # the rejected value is never reflected back
    assert "4111111111111111" not in text  # nor any other field's raw value
    # No stack trace or Python exception class/path leaks (the field-type wording is safe).
    assert "Traceback" not in text
    assert "InvalidOperation" not in text and ".py" not in text
    payload = resp.json()
    assert set(payload) == _ENVELOPE_KEYS
    assert all(set(item) == {"field", "message"} for item in payload["details"])


def test_inference_and_drift_tables_carry_no_phi_columns() -> None:
    for model in (ModelInferenceLog, DriftReport):
        columns = set(model.__table__.columns.keys())
        leaks = {col for col in columns for frag in _PHI_COLUMN_FRAGMENTS if frag in col}
        assert not leaks, f"{model.__tablename__} exposes PHI-shaped columns: {leaks}"
    # The inference log keys the model output by a one-way feature hash, never the raw features.
    assert "feature_hash" in ModelInferenceLog.__table__.columns
