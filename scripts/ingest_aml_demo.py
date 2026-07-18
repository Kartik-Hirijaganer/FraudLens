"""Summary: Bounded, idempotent IBM AML-Data demo ingestion across three synthetic tenants.
The command verifies the locally fetched `HI-Small_Trans.csv`, selects the deterministic
representative CASE PACK (complete laundering-account time neighborhoods plus benign stride
controls — never the old all-negative CSV prefix), validates rows through `build_canonical`, and
persists them only through `TransactionRepository.ingest`, so raw account identifiers are masked
before storage. The public dataset's laundering label steers offline selection only — it is
deliberately not persisted and never converted into an alert; labels remain human-review outcomes.

Key classes:
- DemoIngestRequest: validated CLI request for a bounded number of public-dataset rows.
- AgencyIngestSummary: PHI-free inserted/duplicate counts for one synthetic tenant.
- AmlDemoIngestSummary: PHI-free aggregate result recorded with the import job.

Key functions:
- ensure_demo_agencies: idempotently create the three deterministic synthetic tenant roots.
- ingest_demo_transactions: persist mapped rows through tenant-bound repositories.
- main: verify local data, ingest it into the configured database, and record the job.

Notes:
- Raw IBM rows remain in gitignored `.local/aml_data/` and are never logged or copied.
- External ids are deterministic hashes; source bank/account values live only in memory and are
  masked by the canonical repository path before any database write.
- The command refuses production environment mode and is intended for local Supabase/demo testing.
"""

from __future__ import annotations

import argparse
import asyncio

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import fetch_dataset
from fraudlens_backend.db.models import Agency, JobExecution, JobStatus, JobType
from fraudlens_backend.db.repositories import TransactionRepository
from fraudlens_backend.db.session import build_sessionmaker, create_engine_from_settings
from fraudlens_backend.demo import AML_DEMO_AGENCIES
from fraudlens_backend.settings import get_settings
from fraudlens_core import SchemaValidationError
from lib.aml_fraud import IBM_AML, IbmDemoTransaction, load_ibm_case_pack

_DEFAULT_ROWS = 1600
_MAX_DEMO_ROWS = 10_000


class DemoIngestRequest(BaseModel):
    """Validated request for a bounded prefix of the fetched public dataset."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rows: int = Field(
        default=_DEFAULT_ROWS,
        ge=1,
        le=_MAX_DEMO_ROWS,
        description="Maximum real dataset rows ingested for interactive demo use.",
    )


class AgencyIngestSummary(BaseModel):
    """PHI-free inserted/duplicate counts for one synthetic tenant."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    agency_index: int = Field(..., ge=0, description="Deterministic tenant partition index.")
    accepted: int = Field(..., ge=0, description="Rows newly inserted for this tenant.")
    duplicates: int = Field(..., ge=0, description="Rows already present for this tenant.")


class AmlDemoIngestSummary(BaseModel):
    """PHI-free aggregate result recorded on the global multi-tenant import job."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(..., description="Public dataset source identifier.")
    processed: int = Field(..., ge=0, description="Mapped rows processed across all tenants.")
    accepted: int = Field(..., ge=0, description="Rows newly inserted across all tenants.")
    duplicates: int = Field(..., ge=0, description="Rows already present across all tenants.")
    agencies: list[AgencyIngestSummary] = Field(
        ..., description="Per-tenant counts without raw source identifiers."
    )


async def ensure_demo_agencies(session: AsyncSession) -> None:
    """Idempotently create the deterministic synthetic agencies used by IBM row partitioning."""
    for spec in AML_DEMO_AGENCIES:
        existing = await session.get(Agency, spec.agency_id)
        if existing is not None:
            continue
        slug_owner = (
            await session.execute(select(Agency).where(Agency.slug == spec.slug))
        ).scalar_one_or_none()
        if slug_owner is not None:
            raise RuntimeError("configured AML demo agency slug is already assigned")
        session.add(Agency(id=spec.agency_id, name=spec.name, slug=spec.slug))
    await session.flush()


async def ingest_demo_transactions(
    session: AsyncSession, transactions: list[IbmDemoTransaction]
) -> AmlDemoIngestSummary:
    """Persist mapped rows through one tenant-bound repository per deterministic agency."""
    await ensure_demo_agencies(session)
    repositories = [TransactionRepository(session, spec.agency_id) for spec in AML_DEMO_AGENCIES]
    accepted = [0] * len(repositories)
    duplicates = [0] * len(repositories)
    for transaction in transactions:
        if transaction.agency_index >= len(repositories):
            raise ValueError("mapped demo agency index is outside the configured tenant set")
        outcome = await repositories[transaction.agency_index].ingest(transaction.canonical)
        if outcome.created:
            accepted[transaction.agency_index] += 1
        else:
            duplicates[transaction.agency_index] += 1
    agencies = [
        AgencyIngestSummary(
            agency_index=index,
            accepted=accepted[index],
            duplicates=duplicates[index],
        )
        for index in range(len(repositories))
    ]
    return AmlDemoIngestSummary(
        source=IBM_AML,
        processed=len(transactions),
        accepted=sum(accepted),
        duplicates=sum(duplicates),
        agencies=agencies,
    )


async def _amain(request: DemoIngestRequest) -> int:
    """Verify local IBM data, ingest the bounded prefix, and record a PHI-free job summary."""
    settings = get_settings()
    if settings.environment == "prod":
        print("AML demo ingest refused: never imports public demo data in prod")
        return 1
    spec = fetch_dataset.dataset_spec(IBM_AML)
    try:
        paths = fetch_dataset._verify_present(
            spec, fetch_dataset._data_dir(settings, override=None)
        )
        transactions = load_ibm_case_pack(
            paths,
            rows=request.rows,
            agency_count=len(AML_DEMO_AGENCIES),
        )
    except (FileNotFoundError, OSError, SchemaValidationError, ValueError) as exc:
        print(f"AML demo ingest failed: {exc}")
        return 1
    engine = create_engine_from_settings(settings)
    if engine is None:
        print("AML demo ingest failed: DATABASE_URL is not configured")
        return 1
    sessionmaker = build_sessionmaker(engine)
    try:
        async with sessionmaker() as session:
            summary = await ingest_demo_transactions(session, transactions)
            session.add(
                JobExecution(
                    agency_id=None,
                    job_type=JobType.CSV_IMPORT,
                    status=JobStatus.SUCCEEDED,
                    payload={
                        "source": IBM_AML,
                        "requestedRows": request.rows,
                        "datasetSha256": paths.files[0].sha256,
                    },
                    result=summary.model_dump(mode="json"),
                    attempts=1,
                )
            )
            await session.commit()
    except RuntimeError as exc:
        print(f"AML demo ingest failed: {exc}")
        return 1
    finally:
        await engine.dispose()
    print(
        f"AML demo ingest OK: {summary.accepted} inserted, {summary.duplicates} duplicate, "
        f"{len(summary.agencies)} tenant partitions"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for bounded real IBM AML demo ingestion."""
    parser = argparse.ArgumentParser(description="Ingest real IBM AML rows into demo tenants.")
    parser.add_argument(
        "--rows",
        type=int,
        default=_DEFAULT_ROWS,
        help=f"Rows to ingest (1-{_MAX_DEMO_ROWS}; default {_DEFAULT_ROWS}).",
    )
    args = parser.parse_args(argv)
    request = DemoIngestRequest(rows=args.rows)
    return asyncio.run(_amain(request))


if __name__ == "__main__":
    raise SystemExit(main())
