"""Summary: The synthetic IEEE-CIS transaction importer (plan §16 Phase 3). It maps a
documented subset of IEEE-CIS columns onto the canonical transaction schema and ingests
them through the SAME masked-only path as the API (`fraudlens_core.build_canonical` +
`TransactionRepository.ingest`), so every imported row is deduped by `(agency_id,
externalId)` and stored with masked account identifiers + a feature hash — never raw PHI.
It is partial-accept: a malformed row becomes a PHI-free rejection rather than aborting the
import. `seed_sample_transactions` lets `scripts/seed.py` load the curated sample so
`make local-demo` shows real transactions; the CLI (`make import-ieee`) imports an arbitrary
CSV into the demo agency and records a `csv_import` row in `job_executions`. Refuses to run
against `environment == "prod"` (synthetic-data-only, like the seed).

Key classes:
- ImportResult: counts (+ bounded PHI-free rejections) from an import run.

Key functions:
- map_ieee_row: map one IEEE-CIS row to a CanonicalTransaction (raises on a bad row).
- ingest_rows: ingest an iterable of IEEE-CIS rows into one agency (partial-accept).
- load_sample_rows: read the curated synthetic IEEE-CIS sample CSV shipped in the repo.
- seed_sample_transactions: ingest the curated sample into an agency (used by the seed).
- main: CLI entry — import a CSV into the demo agency and record the job (dev/demo only).

Notes:
- IEEE-CIS has no counterparty account, so originAccount←card1 and destAccount←addr1 (the
  available identifiers) and currency/country default to USD/US (the dataset is US-dollar);
  real deployments map their own account columns. The fraud label is ignored at ingest —
  labels come from human review (plan §10.4), not from the raw dataset.
- occurredAt is derived from TransactionDT (seconds after the IEEE-CIS reference date).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import JobExecution, JobStatus, JobType
from fraudlens_backend.db.repositories import TransactionRepository
from fraudlens_backend.db.session import build_sessionmaker, create_engine_from_settings
from fraudlens_backend.demo import DEMO_AGENCY_ID
from fraudlens_backend.settings import get_settings
from fraudlens_core import CanonicalTransaction, SchemaValidationError, build_canonical

REPO_ROOT = Path(__file__).resolve().parents[1]
_SAMPLE_CSV = REPO_ROOT / "data" / "ieee_cis_sample.csv"

# IEEE-CIS reference epoch: TransactionDT is seconds elapsed after this instant.
_IEEE_EPOCH = datetime(2017, 12, 1, tzinfo=UTC)
# IEEE-CIS amounts are US dollars; the dataset is US-centric (documented defaults).
_DEFAULT_CURRENCY = "USD"
_DEFAULT_COUNTRY = "US"
_FEATURE_COLUMNS = ("ProductCD", "card4", "card6", "P_emaildomain", "dist1")
_SAMPLE_REJECTION_LIMIT = 10


class ImportResult(BaseModel):
    """Counts and bounded, PHI-free rejections from an import run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    accepted: int = Field(..., ge=0, description="Rows newly inserted.")
    duplicates: int = Field(..., ge=0, description="Rows skipped as already-existing.")
    rejected: int = Field(..., ge=0, description="Rows rejected as invalid.")
    rejections: list[dict[str, str]] = Field(
        default_factory=list, description="Bounded sample of PHI-free per-row rejections."
    )


def _occurred_at(transaction_dt: str) -> datetime:
    """Derive occurredAt from TransactionDT (seconds after the IEEE-CIS epoch)."""
    try:
        seconds = int(float(transaction_dt))
    except (TypeError, ValueError) as exc:
        raise SchemaValidationError("transactionDt", "not_a_number") from exc
    return _IEEE_EPOCH + timedelta(seconds=seconds)


def _required(row: Mapping[str, Any], name: str) -> str:
    """Return a non-empty string column value, else raise SchemaValidationError."""
    value = row.get(name)
    if value is None or str(value).strip() == "":
        raise SchemaValidationError(name, "required")
    return str(value)


def map_ieee_row(row: Mapping[str, Any]) -> CanonicalTransaction:
    """Map one IEEE-CIS row to a CanonicalTransaction (the documented subset; may raise)."""
    features = {
        column: row[column] for column in _FEATURE_COLUMNS if row.get(column) not in (None, "")
    }
    return build_canonical(
        external_id=_required(row, "TransactionID"),
        amount=_required(row, "TransactionAmt"),
        currency=_DEFAULT_CURRENCY,
        occurred_at=_occurred_at(_required(row, "TransactionDT")),
        origin_account=_required(row, "card1"),
        dest_account=_required(row, "addr1"),
        channel=_required(row, "ProductCD"),
        country=_DEFAULT_COUNTRY,
        features=features,
    )


async def ingest_rows(
    session: AsyncSession, agency_id: uuid.UUID, rows: Iterable[Mapping[str, Any]]
) -> ImportResult:
    """Ingest IEEE-CIS rows into one agency through the masked-only path (partial-accept)."""
    repo = TransactionRepository(session, agency_id)
    accepted = duplicates = rejected = 0
    rejections: list[dict[str, str]] = []
    for index, row in enumerate(rows):
        try:
            canonical = map_ieee_row(row)
        except SchemaValidationError as exc:
            rejected += 1
            if len(rejections) < _SAMPLE_REJECTION_LIMIT:
                rejections.append({"index": str(index), "field": exc.field, "reason": exc.reason})
            continue
        outcome = await repo.ingest(canonical)
        accepted += outcome.created
        duplicates += not outcome.created
    return ImportResult(
        accepted=accepted, duplicates=duplicates, rejected=rejected, rejections=rejections
    )


def load_sample_rows(csv_path: Path | None = None) -> list[dict[str, Any]]:
    """Read the curated synthetic IEEE-CIS sample CSV (or a given path) into row dicts."""
    path = csv_path or _SAMPLE_CSV
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


async def seed_sample_transactions(session: AsyncSession, agency_id: uuid.UUID) -> int:
    """Ingest the curated sample into an agency (idempotent); return the rows ensured."""
    result = await ingest_rows(session, agency_id, load_sample_rows())
    return result.accepted + result.duplicates


async def _amain(csv_path: Path, agency_id: uuid.UUID) -> int:
    """Import a CSV into the demo agency, record the csv_import job, and print a summary."""
    settings = get_settings()
    if settings.environment == "prod":
        print("import refused: never imports synthetic data in prod (FraudLens governance)")
        return 1
    engine = create_engine_from_settings(settings)
    if engine is None:
        print("import failed: DATABASE_URL is not configured")
        return 1
    sessionmaker = build_sessionmaker(engine)
    try:
        async with sessionmaker() as session:
            result = await ingest_rows(session, agency_id, load_sample_rows(csv_path))
            session.add(
                JobExecution(
                    agency_id=agency_id,
                    job_type=JobType.CSV_IMPORT,
                    status=JobStatus.SUCCEEDED,
                    payload={"source": csv_path.name},
                    result=result.model_dump(),
                    attempts=1,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()
    print(
        f"import OK: {result.accepted} inserted, {result.duplicates} duplicate, "
        f"{result.rejected} rejected"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: import an IEEE-CIS CSV into the demo agency (dev/demo only)."""
    parser = argparse.ArgumentParser(description="Import a synthetic IEEE-CIS CSV.")
    parser.add_argument(
        "csv_path", nargs="?", default=str(_SAMPLE_CSV), help="CSV to import (default: sample)."
    )
    parser.add_argument(
        "--agency-id", default=str(DEMO_AGENCY_ID), help="Target agency id (default: demo)."
    )
    args = parser.parse_args(argv)
    return asyncio.run(_amain(Path(args.csv_path), uuid.UUID(args.agency_id)))


if __name__ == "__main__":
    raise SystemExit(main())
