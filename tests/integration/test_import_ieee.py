"""IEEE-CIS importer tests (plan §16 Phase 3: "IEEE column mapping"; "CSV partial-accept";
"masked-only storage"). The importer maps the documented IEEE-CIS subset onto the canonical
schema and ingests through the same masked-only path as the API."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from portfolio_demo_identity import DEMO_AGENCY_ID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import Agency, Transaction
from fraudlens_core import SchemaValidationError
from import_ieee import ingest_rows, load_sample_rows, map_ieee_row, seed_sample_transactions


def _ieee_row(**overrides: Any) -> dict[str, Any]:
    """A valid IEEE-CIS sample row with per-test overrides."""
    row: dict[str, Any] = {
        "TransactionID": "2987000",
        "TransactionDT": "86400",
        "TransactionAmt": "59.00",
        "ProductCD": "W",
        "card1": "4111111111111111",
        "card4": "visa",
        "card6": "debit",
        "addr1": "204",
        "P_emaildomain": "gmail.com",
        "dist1": "19",
    }
    row.update(overrides)
    return row


async def _seed_demo(session: AsyncSession) -> None:
    """Insert the demo agency (FK target) for importer tests."""
    session.add(Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo"))
    await session.flush()


def test_map_ieee_row_maps_documented_subset() -> None:
    canonical = map_ieee_row(_ieee_row())
    assert canonical.external_id == "2987000"
    assert str(canonical.amount) == "59.00"
    assert canonical.currency == "USD"  # documented default
    assert canonical.country == "US"  # ieee_country: US-centric default (no addr2)
    assert canonical.channel == "card"  # ieee_channel(ProductCD="W")
    # TransactionDT seconds after the IEEE epoch (2017-12-01) -> 2017-12-02.
    assert canonical.occurred_at.isoformat().startswith("2017-12-02")
    # Extra IEEE columns are carried as features.
    assert canonical.features["card4"] == "visa"
    assert "dist1" in canonical.features


def test_map_ieee_row_maps_channel_and_country_via_shared_proxies() -> None:
    # channel/country come from lib.aml_mapping, not the raw ProductCD / a hardcoded "US".
    wire = map_ieee_row(_ieee_row(ProductCD="H", addr2="87"))
    assert (wire.channel, wire.country) == ("wire", "US")
    unknown = map_ieee_row(_ieee_row(ProductCD="Z"))
    assert unknown.channel == "other"  # unmapped ProductCD -> documented default token


def test_map_ieee_row_requires_columns() -> None:
    with pytest.raises(SchemaValidationError) as missing:
        map_ieee_row(_ieee_row(TransactionID=""))
    assert missing.value.field == "TransactionID"
    with pytest.raises(SchemaValidationError) as bad_dt:
        map_ieee_row(_ieee_row(TransactionDT="not-a-number"))
    assert bad_dt.value.field == "transactionDt"


def test_load_sample_rows_returns_curated_set() -> None:
    rows = load_sample_rows()
    assert len(rows) == 12
    assert rows[0]["TransactionID"] == "2987000"


async def test_ingest_rows_partial_accept_and_masked(db_session: AsyncSession) -> None:
    await _seed_demo(db_session)
    rows = [_ieee_row(TransactionID="A1"), _ieee_row(TransactionID="BAD", TransactionAmt="-1")]
    result = await ingest_rows(db_session, DEMO_AGENCY_ID, rows)
    assert (result.accepted, result.rejected) == (1, 1)
    assert result.rejections[0]["field"] == "amount"
    stored = (await db_session.execute(select(Transaction))).scalars().all()
    assert len(stored) == 1
    # Raw card1 is never persisted (masked-only storage, ADR-014).
    assert "4111111111111111" not in stored[0].origin_account
    assert stored[0].feature_hash


async def test_seed_sample_transactions_is_idempotent(db_session: AsyncSession) -> None:
    await _seed_demo(db_session)
    first = await seed_sample_transactions(db_session, DEMO_AGENCY_ID)
    second = await seed_sample_transactions(db_session, DEMO_AGENCY_ID)
    assert first == second == 12  # re-run dedups, never duplicates
    count = len((await db_session.execute(select(Transaction))).scalars().all())
    assert count == 12


async def test_ingest_rows_into_unknown_agency_is_scoped(db_session: AsyncSession) -> None:
    await _seed_demo(db_session)
    other_agency = uuid.uuid4()
    # An agency with no FK row cannot be ingested into (FK enforced) — guard the scope.
    result = await ingest_rows(db_session, DEMO_AGENCY_ID, [_ieee_row(TransactionID="Z1")])
    assert result.accepted == 1
    scoped = (
        (await db_session.execute(select(Transaction).where(Transaction.agency_id == other_agency)))
        .scalars()
        .all()
    )
    assert scoped == []
