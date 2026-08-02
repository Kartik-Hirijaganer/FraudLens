"""Summary: Bounded, idempotent IBM AML-Data demo ingestion into the CONFIGURED demo tenant.
The command verifies the locally fetched `HI-Small_Trans.csv`, selects the deterministic
representative CASE PACK (complete laundering-account time neighborhoods plus benign stride
controls — never the old all-negative CSV prefix), validates rows through `build_canonical`, and
persists them only through `TransactionRepository.ingest`, so raw account identifiers are masked
before storage. The public dataset's laundering label steers offline selection only — it is
deliberately not persisted and never converted into an alert; labels remain human-review outcomes.

Key classes:
- DemoIngestRequest: validated CLI request for a bounded number of public-dataset rows.
- AmlDemoIngestSummary: PHI-free aggregate result recorded with the import job.

Key functions:
- ensure_demo_agency: idempotently create the configured synthetic tenant root.
- ingest_demo_transactions: persist mapped rows through the tenant-bound repository.
- main: verify local data, ingest it into the configured database, and record the job.

Notes:
- Every tenant value (agency identity, partition count, anchor weights) comes from
  `config/portfolio-demo.yaml`; this script holds no demo identity literal.
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
from fraudlens_backend.portfolio_demo import PortfolioDemoConfig, load_portfolio_demo_config
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


class AmlDemoIngestSummary(BaseModel):
    """PHI-free aggregate result recorded on the demo tenant's import job."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(..., description="Public dataset source identifier.")
    processed: int = Field(..., ge=0, description="Mapped rows processed for the tenant.")
    accepted: int = Field(..., ge=0, description="Rows newly inserted for the tenant.")
    duplicates: int = Field(..., ge=0, description="Rows already present for the tenant.")


async def ensure_demo_agency(session: AsyncSession, config: PortfolioDemoConfig) -> None:
    """Idempotently create the configured synthetic tenant the IBM rows are ingested into."""
    agency = config.agency
    if await session.get(Agency, agency.id) is not None:
        return
    slug_owner = (
        await session.execute(select(Agency).where(Agency.slug == agency.slug))
    ).scalar_one_or_none()
    if slug_owner is not None:
        raise RuntimeError("configured AML demo agency slug is already assigned")
    session.add(Agency(id=agency.id, name=agency.name, slug=agency.slug))
    await session.flush()


async def ingest_demo_transactions(
    session: AsyncSession,
    transactions: list[IbmDemoTransaction],
    config: PortfolioDemoConfig,
) -> AmlDemoIngestSummary:
    """Persist every mapped row through the one tenant-bound repository."""
    await ensure_demo_agency(session, config)
    repository = TransactionRepository(session, config.agency.id)
    accepted = duplicates = 0
    for transaction in transactions:
        if transaction.agency_index >= config.case_pack_partition_count:
            raise ValueError("mapped demo agency index is outside the configured tenant set")
        outcome = await repository.ingest(transaction.canonical)
        if outcome.created:
            accepted += 1
        else:
            duplicates += 1
    return AmlDemoIngestSummary(
        source=IBM_AML,
        processed=len(transactions),
        accepted=accepted,
        duplicates=duplicates,
    )


async def _amain(request: DemoIngestRequest) -> int:
    """Verify local IBM data, ingest the bounded prefix, and record a PHI-free job summary."""
    settings = get_settings()
    if settings.environment == "prod":
        print("AML demo ingest refused: never imports public demo data in prod")
        return 1
    config = load_portfolio_demo_config(settings=settings)
    spec = fetch_dataset.dataset_spec(IBM_AML)
    try:
        paths = fetch_dataset._verify_present(
            spec, fetch_dataset._data_dir(settings, override=None)
        )
        transactions = load_ibm_case_pack(
            paths,
            rows=request.rows,
            agency_count=config.case_pack_partition_count,
            tenant_weights=config.case_pack_tenant_weights,
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
            summary = await ingest_demo_transactions(session, transactions, config)
            session.add(
                JobExecution(
                    agency_id=config.agency.id,
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
        f"AML demo ingest OK: {summary.accepted} inserted, {summary.duplicates} duplicate "
        f"(of {summary.processed} processed)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for bounded real IBM AML demo ingestion."""
    parser = argparse.ArgumentParser(description="Ingest real IBM AML rows into the demo tenant.")
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
