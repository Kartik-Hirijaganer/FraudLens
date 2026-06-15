"""Direct-call tests for the transaction handler coroutines (plan §16 Phase 3). These call
the endpoint functions in-loop (like the repository tests) to exercise — and cover — every
handler branch: dedup, partial-accept, dryRun, CSV size/row/type caps, pagination, and the
not-found path. The HTTP wiring (auth, envelope rendering, cross-tenant routing) is covered
separately in test_transactions_api.py."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from fraudlens_backend.api.v1.transactions import (
    get_transaction,
    ingest_batch,
    ingest_transaction,
    list_transactions,
    upload_csv,
)
from fraudlens_backend.db.models import Agency, JobExecution, Transaction
from fraudlens_backend.models.common import TenantContext
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.models.transactions import BatchIngestRequest, TransactionIngestRequest
from fraudlens_backend.settings import AppSettings
from fraudlens_core import RiskBand, SchemaValidationError

CSV_HEADER = "externalId,amount,currency,occurredAt,originAccount,destAccount,channel,country"


async def _tenant(session: AsyncSession) -> TenantContext:
    """Insert an agency and return its tenant context (the handler scope)."""
    agency = Agency(id=uuid.uuid4(), name="Acme", slug=f"a-{uuid.uuid4().hex[:8]}")
    session.add(agency)
    await session.flush()
    return TenantContext(agency_id=str(agency.id))


def _request_model(**overrides: Any) -> TransactionIngestRequest:
    """Build a valid TransactionIngestRequest with per-test overrides."""
    params: dict[str, Any] = {
        "external_id": "T1",
        "amount": "100.50",
        "currency": "usd",
        "occurred_at": datetime(2026, 1, 1, tzinfo=UTC),
        "origin_account": "4111111111111111",
        "dest_account": "987654321",
        "channel": "wire",
        "country": "us",
    }
    params.update(overrides)
    return TransactionIngestRequest(**params)


def _row(**overrides: Any) -> dict[str, Any]:
    """A raw camelCase batch/CSV row dict with overrides."""
    row: dict[str, Any] = {
        "externalId": "R1",
        "amount": "10.00",
        "currency": "USD",
        "occurredAt": "2026-01-01T00:00:00+00:00",
        "originAccount": "4111111111111111",
        "destAccount": "987654321",
        "channel": "wire",
        "country": "US",
    }
    row.update(overrides)
    return row


def _request() -> Request:
    """A minimal Starlette request (no body / request-id) for the single/batch ingest handlers.

    Single/batch ingest take the already-parsed payload + a request only for the audit
    correlation id (Phase 12), so they never read the body; the CSV handler uses `_csv_request`.
    """
    return Request(
        {"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}
    )


def _csv_request(body: str, content_type: str = "text/csv") -> Request:
    """Build a minimal Starlette Request carrying a raw CSV body."""
    raw = body.encode("utf-8")

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": raw, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/transactions/upload",
        "query_string": b"",
        "headers": [(b"content-type", content_type.encode("ascii"))],
    }
    return Request(scope, receive)


async def test_ingest_single_and_duplicate(
    db_session: AsyncSession, make_settings: Callable[..., AppSettings]
) -> None:
    tenant = await _tenant(db_session)
    created = await ingest_transaction(_request_model(), _request(), tenant, db_session)
    assert created.origin_account.endswith("1111")
    assert "4111111111111111" not in created.origin_account
    assert created.risk_band is None
    with pytest.raises(AppError) as excinfo:
        await ingest_transaction(_request_model(), _request(), tenant, db_session)
    assert excinfo.value.code == "duplicate_external_id"


async def test_ingest_future_date_raises_schema_error(db_session: AsyncSession) -> None:
    tenant = await _tenant(db_session)
    with pytest.raises(SchemaValidationError) as excinfo:
        await ingest_transaction(
            _request_model(occurred_at=datetime(2999, 1, 1, tzinfo=UTC)),
            _request(),
            tenant,
            db_session,
        )
    assert excinfo.value.field == "occurred_at"


async def test_batch_partial_accept_and_duplicate(
    db_session: AsyncSession, make_settings: Callable[..., AppSettings]
) -> None:
    tenant = await _tenant(db_session)
    payload = BatchIngestRequest(
        transactions=[
            _row(externalId="OK1"),
            _row(externalId="BAD", amount="-1"),
            _row(externalId="OK1"),
        ]
    )
    result = await ingest_batch(payload, _request(), tenant, db_session, make_settings())
    # OK1 inserted once, the second OK1 is a duplicate, BAD is rejected.
    assert (result.accepted, result.duplicates, result.rejected) == (1, 1, 1)
    assert result.sample_errors[0].external_id == "BAD"


async def test_batch_rejects_missing_field_and_bad_date(
    db_session: AsyncSession, make_settings: Callable[..., AppSettings]
) -> None:
    tenant = await _tenant(db_session)
    no_external_id = {
        "amount": "1",
        "currency": "USD",
        "occurredAt": "2026-01-01T00:00:00+00:00",
        "originAccount": "1",
        "destAccount": "2",
        "channel": "c",
        "country": "US",
    }
    payload = BatchIngestRequest(
        transactions=[no_external_id, _row(externalId="BADDATE", occurredAt="not-a-date")]
    )
    result = await ingest_batch(payload, _request(), tenant, db_session, make_settings())
    assert (result.accepted, result.rejected) == (0, 2)
    fields = {error.message.split(":")[0] for error in result.sample_errors}
    assert fields == {"externalId", "occurredAt"}


async def test_batch_sample_errors_are_bounded(
    db_session: AsyncSession, make_settings: Callable[..., AppSettings]
) -> None:
    tenant = await _tenant(db_session)
    payload = BatchIngestRequest(
        transactions=[_row(externalId="B1", amount="-1"), _row(externalId="B2", amount="-2")]
    )
    result = await ingest_batch(
        payload, _request(), tenant, db_session, make_settings(ingest_sample_errors_limit=1)
    )
    assert result.rejected == 2
    assert len(result.sample_errors) == 1  # bounded — the second rejection is not sampled


async def test_batch_dry_run_persists_nothing(
    db_session: AsyncSession, make_settings: Callable[..., AppSettings]
) -> None:
    tenant = await _tenant(db_session)
    await ingest_transaction(_request_model(external_id="EXISTS"), _request(), tenant, db_session)
    payload = BatchIngestRequest(
        dry_run=True, transactions=[_row(externalId="EXISTS"), _row(externalId="NEW")]
    )
    result = await ingest_batch(payload, _request(), tenant, db_session, make_settings())
    assert (result.accepted, result.duplicates, result.dry_run) == (1, 1, True)
    count = (await db_session.execute(select(func.count()).select_from(Transaction))).scalar_one()
    assert count == 1  # only EXISTS; dryRun added nothing


async def test_batch_too_large_raises(
    db_session: AsyncSession, make_settings: Callable[..., AppSettings]
) -> None:
    tenant = await _tenant(db_session)
    payload = BatchIngestRequest(transactions=[_row(externalId="A"), _row(externalId="B")])
    with pytest.raises(AppError) as excinfo:
        await ingest_batch(
            payload, _request(), tenant, db_session, make_settings(ingest_max_batch_size=1)
        )
    assert excinfo.value.code == "batch_too_large"


async def test_upload_csv_partial_accept_records_job(
    db_session: AsyncSession, make_settings: Callable[..., AppSettings]
) -> None:
    tenant = await _tenant(db_session)
    body = (
        f"{CSV_HEADER}\n"
        "C1,10.00,USD,2026-01-02T00:00:00+00:00,4111111111111111,55667788,card,US\n"
        "C1,10.00,USD,2026-01-02T00:00:00+00:00,4111111111111111,55667788,card,US\n"
        "C2,bad,USD,2026-01-02T00:00:00+00:00,1,2,card,US\n"
    )
    result = await upload_csv(_csv_request(body), tenant, db_session, make_settings())
    assert (result.accepted, result.duplicates, result.rejected) == (1, 1, 1)
    assert result.job_id
    jobs = (await db_session.execute(select(func.count()).select_from(JobExecution))).scalar_one()
    assert jobs == 1


async def test_upload_csv_accepts_empty_content_type(
    db_session: AsyncSession, make_settings: Callable[..., AppSettings]
) -> None:
    tenant = await _tenant(db_session)
    body = (
        f"{CSV_HEADER}\nC1,10.00,USD,2026-01-02T00:00:00+00:00,4111111111111111,55667788,card,US\n"
    )
    result = await upload_csv(
        _csv_request(body, content_type=""), tenant, db_session, make_settings()
    )
    assert result.accepted == 1


_ROW = "X,1,USD,2026-01-01T00:00:00+00:00,1,2,c,US"


@pytest.mark.parametrize(
    ("body", "content_type", "overrides", "code"),
    [
        (f"{CSV_HEADER}\n{_ROW}\n", "text/csv", {"ingest_csv_max_bytes": 16}, "payload_too_large"),
        (
            f"{CSV_HEADER}\n{_ROW}\n{_ROW}\n",
            "text/csv",
            {"ingest_csv_max_rows": 1},
            "too_many_rows",
        ),
        ("{}", "application/json", {}, "unsupported_content_type"),
        (f"{CSV_HEADER}\n", "text/csv", {}, "empty_payload"),
        ("", "text/csv", {}, "invalid_csv"),
    ],
)
async def test_upload_csv_caps_and_validation(
    db_session: AsyncSession,
    make_settings: Callable[..., AppSettings],
    body: str,
    content_type: str,
    overrides: dict[str, Any],
    code: str,
) -> None:
    tenant = await _tenant(db_session)
    with pytest.raises(AppError) as excinfo:
        await upload_csv(
            _csv_request(body, content_type), tenant, db_session, make_settings(**overrides)
        )
    assert excinfo.value.code == code


async def test_list_paginates_and_detail(db_session: AsyncSession) -> None:
    tenant = await _tenant(db_session)
    for index in range(3):
        await ingest_transaction(
            _request_model(external_id=f"L{index}"), _request(), tenant, db_session
        )
    page1 = await list_transactions(tenant, db_session, limit=2)
    assert len(page1.transactions) == 2
    assert page1.next_cursor is not None
    page2 = await list_transactions(tenant, db_session, limit=2, cursor=page1.next_cursor)
    assert len(page2.transactions) == 1
    assert page2.next_cursor is None
    tid = uuid.UUID(page1.transactions[0].transaction_id)
    detail = await get_transaction(tid, tenant, db_session)
    assert detail.transaction_id == str(tid)


async def test_list_filters_risk_band_and_renders_scored_fields(db_session: AsyncSession) -> None:
    tenant = await _tenant(db_session)
    created = await ingest_transaction(
        _request_model(external_id="SCORED"), _request(), tenant, db_session
    )
    row = await db_session.get(Transaction, uuid.UUID(created.transaction_id))
    assert row is not None
    row.risk_band = RiskBand.HIGH
    row.latest_run_id = uuid.uuid4()
    await db_session.flush()
    page = await list_transactions(tenant, db_session, risk_band=RiskBand.HIGH)
    assert [t.external_id for t in page.transactions] == ["SCORED"]
    assert page.transactions[0].risk_band == "high"
    assert page.transactions[0].latest_run_id is not None


async def test_get_transaction_missing_raises(db_session: AsyncSession) -> None:
    tenant = await _tenant(db_session)
    with pytest.raises(AppError) as excinfo:
        await get_transaction(uuid.uuid4(), tenant, db_session)
    assert excinfo.value.code == "transaction_not_found"
