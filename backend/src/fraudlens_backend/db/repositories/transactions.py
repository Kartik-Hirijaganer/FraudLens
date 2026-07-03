"""Summary: The agency-scoped transaction repository (plan §16 Phase 3). Built on
`TenantScopedRepository`, so every read/write is bound to one `agency_id` and a
cross-tenant id resolves to nothing. `ingest` is the single persistence path shared by the
API endpoint and the IEEE-CIS importer: it dedups by `(agency_id, external_id)` (returning
the existing row with `created=False` instead of raising), masks the account identifiers +
computes the `feature_hash` via the injected `PhiMasker`, and persists ONLY the masked form
(raw PHI is never written, ADR-014). `page` returns keyset-paginated, newest-first results
with an opaque cursor, optionally filtered by risk band, so the list endpoint never scans
or offsets the whole table.

Key classes:
- IngestOutcome: the result of an ingest — the row and whether it was newly created.
- TransactionRepository: agency-scoped dedup ingest + keyset listing for transactions.

Key functions:
- encode_cursor: encode a (ingested_at, id) position into an opaque page cursor.
- decode_cursor: decode an opaque cursor back to (ingested_at, id), or None if malformed.

Notes:
- `ingest` sets `ingested_at` in Python so the value is available after flush without an
  async lazy-load/refresh round-trip; the column's server default remains the fallback.
- The keyset orders by (ingested_at DESC, id DESC); a malformed cursor is ignored (the
  caller simply gets the first page) rather than erroring.
- Cursor timestamps are ENCODED naive-UTC (a compact, stable cursor string) but DECODED
  back to tz-aware UTC, because the keyset predicate compares against `ingested_at`, a
  tz-aware `timestamptz` column. Binding a naive value makes Postgres/asyncpg read it in
  the session timezone (not UTC), so `ingested_at < after_ts` is true for every row and
  page 2 repeats page 1 forever (an infinite loop). Aware-UTC compares correctly on
  Postgres and remains correct on SQLite (its DateTime formats UTC wall-clock either way).
"""

from __future__ import annotations

import base64
import binascii
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import Transaction
from fraudlens_backend.db.repositories.base import TenantScopedRepository
from fraudlens_backend.services.phi_mask import PhiMasker
from fraudlens_core import CanonicalTransaction, RiskBand
from fraudlens_core.phi import MaskingReport

_CURSOR_SEP = "|"


class IngestOutcome(NamedTuple):
    """The result of an ingest: row, creation flag, and counts-only masking report."""

    transaction: Transaction
    created: bool
    mask_report: MaskingReport | None


def _to_naive_utc(value: datetime) -> datetime:
    """Normalize a datetime to naive-UTC (so keyset comparisons are dialect-stable)."""
    if value.tzinfo is not None:
        value = value.astimezone(UTC)
    return value.replace(tzinfo=None)


def encode_cursor(ingested_at: datetime, entity_id: uuid.UUID) -> str:
    """Encode a (ingested_at, id) position into an opaque urlsafe page cursor."""
    raw = f"{_to_naive_utc(ingested_at).isoformat()}{_CURSOR_SEP}{entity_id.hex}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID] | None:
    """Decode an opaque cursor back to (tz-aware-UTC ingested_at, id); None when malformed.

    The timestamp is returned tz-aware UTC (not naive) so the keyset predicate compares
    correctly against the tz-aware `ingested_at` column — see the module docstring for why a
    naive value silently breaks pagination on Postgres.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        timestamp, hex_id = raw.split(_CURSOR_SEP, 1)
        parsed = datetime.fromisoformat(timestamp)
        aware = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
        return aware, uuid.UUID(hex_id)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None


class TransactionRepository(TenantScopedRepository[Transaction]):
    """Agency-scoped dedup ingest + keyset listing for the `transactions` table."""

    def __init__(
        self, session: AsyncSession, agency_id: uuid.UUID, *, masker: PhiMasker | None = None
    ) -> None:
        """Bind the session + agency scope and the PHI masker used at ingest."""
        super().__init__(session, Transaction, agency_id)
        self._masker = masker or PhiMasker()

    async def get_by_external_id(self, external_id: str) -> Transaction | None:
        """Return this agency's transaction with the given externalId, or None."""
        stmt = select(Transaction).where(
            Transaction.agency_id == self._agency_id,
            Transaction.external_id == external_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def ingest(self, canonical: CanonicalTransaction) -> IngestOutcome:
        """Dedup by externalId; otherwise mask + persist the transaction (masked-only)."""
        existing = await self.get_by_external_id(canonical.external_id)
        if existing is not None:
            return IngestOutcome(existing, created=False, mask_report=None)
        masked = self._masker.mask(canonical)
        transaction = Transaction(
            agency_id=self._agency_id,
            external_id=canonical.external_id,
            amount=canonical.amount,
            currency=canonical.currency,
            occurred_at=canonical.occurred_at,
            origin_account=masked.origin_account,
            dest_account=masked.dest_account,
            channel=canonical.channel,
            country=canonical.country,
            features=masked.features,
            feature_hash=masked.feature_hash,
            ingested_at=datetime.now(UTC),
        )
        self._session.add(transaction)
        await self._session.flush()
        return IngestOutcome(transaction, created=True, mask_report=masked.report)

    async def same_account_history(
        self, *, account: str, before: datetime, window_hours: int, limit: int
    ) -> Sequence[Transaction]:
        """Return this agency's recent transactions touching `account` before `before` (windowed).

        Groups by the masked account identifier (masking is deterministic, ADR-014), matching it
        as either origin OR destination so the pipeline can label each prior as outbound/inbound —
        the same-account history the rules engine + feature extractor expect (plan §16 Phase 8).
        """
        start = before - timedelta(hours=window_hours)
        stmt = (
            select(Transaction)
            .where(
                Transaction.agency_id == self._agency_id,
                Transaction.occurred_at < before,
                Transaction.occurred_at >= start,
                or_(
                    Transaction.origin_account == account,
                    Transaction.dest_account == account,
                ),
            )
            .order_by(Transaction.occurred_at.desc())
            .limit(limit)
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def page(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        risk_band: RiskBand | None = None,
    ) -> tuple[Sequence[Transaction], str | None]:
        """Return one keyset page (newest first) + the next cursor (None when exhausted)."""
        stmt = select(Transaction).where(Transaction.agency_id == self._agency_id)
        if risk_band is not None:
            stmt = stmt.where(Transaction.risk_band == risk_band)
        decoded = decode_cursor(cursor) if cursor else None
        if decoded is not None:
            after_ts, after_id = decoded
            stmt = stmt.where(
                or_(
                    Transaction.ingested_at < after_ts,
                    and_(Transaction.ingested_at == after_ts, Transaction.id < after_id),
                )
            )
        stmt = stmt.order_by(Transaction.ingested_at.desc(), Transaction.id.desc()).limit(limit + 1)
        rows = list((await self._session.execute(stmt)).scalars().all())
        if len(rows) > limit:
            last = rows[limit - 1]
            return rows[:limit], encode_cursor(last.ingested_at, last.id)
        return rows, None
