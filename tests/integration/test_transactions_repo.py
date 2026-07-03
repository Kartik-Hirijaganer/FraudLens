"""TransactionRepository tests (plan §16 Phase 3): dedup ingest, masked-only storage,
keyset pagination + riskBand filter, agency scoping, and cursor encode/decode."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

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
    # Pages must be DISJOINT — the cursor bug returned page 1 again on page 2.
    first_ids = {row.external_id for row in first_page}
    second_ids = {row.external_id for row in second_page}
    assert first_ids.isdisjoint(second_ids)
    assert first_ids | second_ids == {"T0", "T1", "T2"}


async def test_page_walks_every_row_without_looping(db_session: AsyncSession) -> None:
    """Follow the cursor to exhaustion: each page is distinct and paging terminates.

    Guards the keyset-loop regression end to end — with the old naive cursor, page 2
    repeated page 1 and `nextCursor` never advanced, so this walk would never terminate
    (and would re-see ids). All rows share a near-identical ingested_at, exercising the
    (ingested_at, id) tiebreak.
    """
    repo = TransactionRepository(db_session, await _agency(db_session))
    total = 5
    for index in range(total):
        await repo.ingest(_canonical(f"T{index}"))

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(total + 2):  # bounded so a looping cursor fails instead of hanging
        rows, cursor = await repo.page(limit=2, cursor=cursor)
        seen.extend(row.external_id for row in rows)
        if cursor is None:
            break

    assert cursor is None, "cursor never exhausted — pagination is looping"
    assert len(seen) == len(set(seen)) == total  # every row seen exactly once


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
    # The cursor decodes back to the same instant, tz-aware UTC (see below for why).
    decoded = decode_cursor(encode_cursor(when, entity_id))
    assert decoded == (when, entity_id)
    assert decode_cursor("not-valid-base64!") is None


def test_cursor_decodes_to_utc_aware() -> None:
    """Regression: the decoded cursor MUST be tz-aware UTC.

    The keyset compares the cursor against the tz-aware `ingested_at` (timestamptz) column.
    A naive value is read by Postgres in the session timezone, landing ahead of every stored
    UTC row so page 2 repeats page 1 forever. SQLite drops tzinfo on read and hides this, so
    this dialect-agnostic assertion — not a SQLite paging test — is what guards the fix.
    """
    cursor = encode_cursor(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC), uuid.uuid4())
    decoded = decode_cursor(cursor)
    assert decoded is not None
    timestamp, _ = decoded
    assert timestamp.tzinfo is not None
    assert timestamp.utcoffset() == timedelta(0)
