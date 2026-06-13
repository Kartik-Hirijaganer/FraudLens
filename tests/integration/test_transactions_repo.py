"""TransactionRepository tests (plan §16 Phase 3): dedup ingest, masked-only storage,
keyset pagination + riskBand filter, agency scoping, and cursor encode/decode."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import Agency
from fraudlens_backend.db.repositories import TransactionRepository
from fraudlens_backend.db.repositories.transactions import decode_cursor, encode_cursor
from fraudlens_core import RiskBand, build_canonical

_NOW = datetime(2026, 6, 12, tzinfo=UTC)


async def _agency(session: AsyncSession) -> uuid.UUID:
    """Insert and flush an agency, returning its id (FK target for transactions)."""
    agency = Agency(name="Acme", slug=f"acme-{uuid.uuid4().hex[:8]}")
    session.add(agency)
    await session.flush()
    return agency.id


def _canonical(external_id: str, *, origin: str = "4111111111111111"):
    """Build a valid CanonicalTransaction for ingest tests."""
    return build_canonical(
        external_id=external_id,
        amount="10.00",
        currency="USD",
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        origin_account=origin,
        dest_account="987654321",
        channel="wire",
        country="US",
        now=_NOW,
    )


async def test_ingest_creates_then_dedups(db_session: AsyncSession) -> None:
    repo = TransactionRepository(db_session, await _agency(db_session))
    first = await repo.ingest(_canonical("T1"))
    second = await repo.ingest(_canonical("T1"))
    assert first.created is True
    assert second.created is False
    assert second.transaction.id == first.transaction.id


async def test_ingest_stores_masked_only(db_session: AsyncSession) -> None:
    repo = TransactionRepository(db_session, await _agency(db_session))
    outcome = await repo.ingest(_canonical("T1", origin="4111111111111111"))
    assert "4111111111111111" not in outcome.transaction.origin_account
    assert outcome.transaction.origin_account.endswith("1111")
    assert outcome.transaction.feature_hash


async def test_page_paginates_with_cursor(db_session: AsyncSession) -> None:
    repo = TransactionRepository(db_session, await _agency(db_session))
    for index in range(3):
        await repo.ingest(_canonical(f"T{index}"))
    first_page, cursor = await repo.page(limit=2)
    assert len(first_page) == 2
    assert cursor is not None
    second_page, end_cursor = await repo.page(limit=2, cursor=cursor)
    assert len(second_page) == 1
    assert end_cursor is None
    seen = {row.external_id for row in (*first_page, *second_page)}
    assert seen == {"T0", "T1", "T2"}


async def test_page_filters_by_risk_band(db_session: AsyncSession) -> None:
    repo = TransactionRepository(db_session, await _agency(db_session))
    high = (await repo.ingest(_canonical("HIGH"))).transaction
    await repo.ingest(_canonical("UNSCORED"))
    high.risk_band = RiskBand.HIGH
    await db_session.flush()
    rows, _ = await repo.page(limit=10, risk_band=RiskBand.HIGH)
    assert [row.external_id for row in rows] == ["HIGH"]
    assert (await repo.page(limit=10, risk_band=RiskBand.LOW))[0] == []


async def test_page_ignores_malformed_cursor(db_session: AsyncSession) -> None:
    repo = TransactionRepository(db_session, await _agency(db_session))
    await repo.ingest(_canonical("T1"))
    rows, _ = await repo.page(limit=10, cursor="!!not-base64!!")
    assert len(rows) == 1


async def test_ingest_is_agency_scoped(db_session: AsyncSession) -> None:
    repo_a = TransactionRepository(db_session, await _agency(db_session))
    repo_b = TransactionRepository(db_session, await _agency(db_session))
    created = (await repo_a.ingest(_canonical("SHARED"))).transaction
    # Same externalId is a distinct row for another agency, and B cannot read A's row.
    assert (await repo_b.ingest(_canonical("SHARED"))).created is True
    assert await repo_b.get(created.id) is None


def test_cursor_roundtrip_and_malformed() -> None:
    when = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    entity_id = uuid.uuid4()
    # The cursor normalizes the timestamp to naive-UTC (dialect-stable keyset).
    decoded = decode_cursor(encode_cursor(when, entity_id))
    assert decoded == (when.replace(tzinfo=None), entity_id)
    assert decode_cursor("not-valid-base64!") is None
