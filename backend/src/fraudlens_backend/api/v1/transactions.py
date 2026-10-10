"""Summary: The transaction ingestion + listing API (plan §5.4, §16 Phase 3; endpoints
1-5). Every route is scoped to the verified JWT `agency_id` (via `get_tenant`, never a
path/body tenant) and persists ONLY masked account identifiers + a feature hash through the
shared `TransactionRepository.ingest` path (ADR-014). Single ingest dedups by externalId
(409 `duplicate_external_id`); batch + CSV ingest are partial-accept — valid rows persist
while invalid ones become bounded, PHI-free per-row rejections, and a CSV upload records a
`csv_import` row in `job_executions`. Semantic validation (ISO codes, positive amount,
not-future timestamp) runs once in `fraudlens_core.build_canonical`, shared with the IEEE
importer; failures surface as a 422 envelope with a field/reason detail (no echoed values).

Key classes:
- (none)

Key functions:
- ingest_transaction: POST /transactions — ingest one (201; 409 on duplicate).
- ingest_batch: POST /transactions/batch — ingest many (partial-accept; dryRun).
- upload_csv: POST /transactions/upload — CSV upload (size/row caps; records a job).
- list_transactions: GET /transactions — keyset page (newest first), optional riskBand.
- get_screening_progress:
- get_transaction: GET /transactions/{transactionId} — detail (404 cross-tenant/missing).

Notes:
- The CSV upload reads the raw text/csv body (no multipart dependency); byte/row caps come
from settings (413), and unknown columns are folded into the masked features JSONB.
- A cross-tenant transaction id resolves to None (tenant-scoped get) and returns 404 with
the same body as a truly missing row — no existence leak (plan §6.4).
- Each successful ingest path (single/batch/CSV) writes a PHI-free `audit_logs` row (ids + counts
only, never an account/value) for the consistent audit trail (plan §11.7, §16 Phase 12).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.api.deps import (
    DbSessionDep,
    Permission,
    SettingsDep,
    audit_writer,
    get_tenant,
    optional_actor,
    require_permission,
)
from fraudlens_backend.db.models import (
    JobExecution,
    JobStatus,
    JobType,
    Transaction,
    TransactionSource,
)
from fraudlens_backend.db.repositories import AuditLogRepository, TransactionRepository
from fraudlens_backend.models.common import TenantContext
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.models.transactions import (
    BatchIngestRequest,
    BatchIngestResponse,
    CsvUploadResponse,
    IngestRejection,
    InvestigationSummary,
    ScreeningProgress,
    TransactionIngestRequest,
    TransactionListResponse,
    TransactionResponse,
)
from fraudlens_backend.services.investigation_start import create_investigation_run
from fraudlens_backend.services.screening import investigation_summaries, screening_progress
from fraudlens_backend.services.transaction_ingest import (
    _append_sample,
    _as_str,
    _mapping_to_canonical,
    _read_csv,
)
from fraudlens_core import RiskBand, SchemaValidationError
from fraudlens_core.phi import MaskingReport

router = APIRouter(tags=["transactions"])

TenantDep = Annotated[TenantContext, Depends(get_tenant)]
IngestTenantDep = Annotated[
    TenantContext, Depends(require_permission(Permission.INGEST_TRANSACTIONS))
]

_DEFAULT_PAGE_LIMIT = 50
_MAX_PAGE_LIMIT = 200
_MAX_SEARCH_LEN = 128


def _to_response(
    transaction: Transaction, investigation: InvestigationSummary | None = None
) -> TransactionResponse:
    """Project a persisted Transaction (masked fields) onto the API response model."""
    return TransactionResponse(
        transaction_id=str(transaction.id),
        external_id=transaction.external_id,
        agency_id=str(transaction.agency_id),
        amount=transaction.amount,
        currency=transaction.currency,
        occurred_at=transaction.occurred_at,
        origin_account=transaction.origin_account,
        dest_account=transaction.dest_account,
        channel=transaction.channel,
        country=transaction.country,
        risk_band=transaction.risk_band.value if transaction.risk_band is not None else None,
        latest_run_id=str(transaction.latest_run_id) if transaction.latest_run_id else None,
        ingested_at=transaction.ingested_at,
        investigation=investigation,
    )


def _repo(tenant: TenantContext, session: AsyncSession) -> TransactionRepository:
    """Build an agency-scoped transaction repository for the verified tenant."""
    return TransactionRepository(session, uuid.UUID(tenant.agency_id))


async def _record_phi_mask(
    writer: AuditLogRepository,
    *,
    actor_id: uuid.UUID | None,
    resource_id: str,
    report: MaskingReport | None,
    source: str,
) -> None:
    """Record a counts-only PHI masking audit row when a masking pass redacted data."""
    if report is None or report.total == 0:
        return
    await writer.record(
        actor_id=actor_id,
        action="phi_mask",
        resource_type="transaction",
        resource_id=resource_id,
        metadata={
            "source": source,
            "maskedCount": str(report.total),
            "categories": ",".join(f"{key}:{value}" for key, value in report.categories.items()),
        },
    )


async def _record_phi_access(  # noqa: PLR0913 - explicit audit fields keep call sites clear.
    writer: AuditLogRepository,
    *,
    actor_id: uuid.UUID | None,
    resource_type: str,
    resource_id: str | None,
    count: int,
    source: str,
) -> None:
    """Record access to masked transaction identifiers without exposing their values."""
    if count == 0:
        return
    await writer.record(
        actor_id=actor_id,
        action="phi_access",
        resource_type=resource_type,
        resource_id=resource_id,
        metadata={
            "source": source,
            "recordCount": str(count),
            "fields": "originAccount,destAccount",
            "masked": "true",
        },
    )


@router.post("/transactions", response_model=TransactionResponse, status_code=201)
async def ingest_transaction(
    payload: TransactionIngestRequest,
    request: Request,
    tenant: IngestTenantDep,
    session: DbSessionDep,
) -> TransactionResponse:
    """Ingest one transaction (201); 409 when its externalId already exists for the agency."""
    repo = _repo(tenant, session)
    outcome = await repo.ingest(
        _mapping_to_canonical(payload.model_dump(by_alias=True)),
        source=TransactionSource.API_UPLOAD,
    )
    if not outcome.created:
        raise AppError("duplicate_external_id")
    writer = audit_writer(tenant, session, request)
    actor_id = optional_actor(tenant)
    await writer.record(
        actor_id=actor_id,
        action="transaction.ingest",
        resource_type="transaction",
        resource_id=str(outcome.transaction.id),
        metadata={"externalId": outcome.transaction.external_id},
    )
    await _record_phi_mask(
        writer,
        actor_id=actor_id,
        resource_id=str(outcome.transaction.id),
        report=outcome.mask_report,
        source="transaction.ingest",
    )
    await session.commit()
    return _to_response(outcome.transaction)


@router.post("/transactions/batch", response_model=BatchIngestResponse)
async def ingest_batch(
    payload: BatchIngestRequest,
    request: Request,
    tenant: IngestTenantDep,
    session: DbSessionDep,
    settings: SettingsDep,
) -> BatchIngestResponse:
    """Ingest a batch (partial-accept); dryRun validates + masks without persisting."""
    if len(payload.transactions) > settings.ingest_max_batch_size:
        raise AppError("batch_too_large")
    repo = _repo(tenant, session)
    writer = audit_writer(tenant, session, request)
    actor_id = optional_actor(tenant)
    accepted = duplicates = rejected = 0
    created: list[TransactionResponse] = []
    samples: list[IngestRejection] = []
    for index, item in enumerate(payload.transactions):
        external_id = item.get("externalId") if isinstance(item, Mapping) else None
        try:
            canonical = _mapping_to_canonical(item)
        except SchemaValidationError as exc:
            rejected += 1
            _append_sample(
                samples, index, _as_str(external_id), exc, settings.ingest_sample_errors_limit
            )
            continue
        if payload.dry_run:
            existing = await repo.get_by_external_id(canonical.external_id)
            duplicates += existing is not None
            accepted += existing is None
            continue
        outcome = await repo.ingest(canonical, source=TransactionSource.API_UPLOAD)
        if outcome.created:
            accepted += 1
            created.append(_to_response(outcome.transaction))
            await _record_phi_mask(
                writer,
                actor_id=actor_id,
                resource_id=str(outcome.transaction.id),
                report=outcome.mask_report,
                source="transaction.batch_ingest",
            )
        else:
            duplicates += 1
    if not payload.dry_run:
        await writer.record(
            actor_id=actor_id,
            action="transaction.batch_ingest",
            resource_type="transaction_batch",
            resource_id=None,
            metadata={
                "accepted": str(accepted),
                "duplicates": str(duplicates),
                "rejected": str(rejected),
            },
        )
        await session.commit()
    return BatchIngestResponse(
        accepted=accepted,
        duplicates=duplicates,
        rejected=rejected,
        dry_run=payload.dry_run,
        transactions=created,
        sample_errors=samples,
    )


@router.post("/transactions/upload", response_model=CsvUploadResponse, status_code=202)
async def upload_csv(
    request: Request, tenant: IngestTenantDep, session: DbSessionDep, settings: SettingsDep
) -> CsvUploadResponse:
    """Ingest a text/csv upload (partial-accept); enforce size/row caps; record a job."""
    rows = await _read_csv(request, settings.ingest_csv_max_bytes, settings.ingest_csv_max_rows)
    repo = _repo(tenant, session)
    writer = audit_writer(tenant, session, request)
    actor_id = optional_actor(tenant)
    accepted = duplicates = rejected = 0
    samples: list[IngestRejection] = []
    job = JobExecution(
        agency_id=uuid.UUID(tenant.agency_id),
        job_type=JobType.CSV_IMPORT,
        status=JobStatus.SUCCEEDED,
        payload={"rowCount": len(rows)},
        attempts=1,
    )
    session.add(job)
    await session.flush()
    accepted_ids: list[uuid.UUID] = []
    for index, row in enumerate(rows):
        try:
            canonical = _mapping_to_canonical(row)
        except SchemaValidationError as exc:
            rejected += 1
            _append_sample(
                samples,
                index,
                _as_str(row.get("externalId")),
                exc,
                settings.ingest_sample_errors_limit,
            )
            continue
        outcome = await repo.ingest(canonical, source=TransactionSource.API_UPLOAD)
        accepted += outcome.created
        duplicates += not outcome.created
        if outcome.created:
            accepted_ids.append(outcome.transaction.id)
            await _record_phi_mask(
                writer,
                actor_id=actor_id,
                resource_id=str(outcome.transaction.id),
                report=outcome.mask_report,
                source="transaction.csv_import",
            )
    job.result = {"accepted": accepted, "duplicates": duplicates, "rejected": rejected}
    for offset in range(0, len(accepted_ids), settings.batch_score_limit):
        for transaction_id in accepted_ids[offset : offset + settings.batch_score_limit]:
            await create_investigation_run(
                manager=request.app.state.run_manager,
                session=session,
                settings=settings,
                tenant=tenant,
                request=request,
                transaction_id=transaction_id,
                idempotency_key=f"csv-screen:{transaction_id}",
                import_job_id=job.id,
            )
    job_id = str(job.id)
    await writer.record(
        actor_id=actor_id,
        action="transaction.csv_import",
        resource_type="job_execution",
        resource_id=job_id,
        metadata={
            "accepted": str(accepted),
            "duplicates": str(duplicates),
            "rejected": str(rejected),
            "rowCount": str(len(rows)),
        },
    )
    await session.commit()
    return CsvUploadResponse(
        job_id=job_id,
        screening=ScreeningProgress(
            total=accepted, pending=accepted, poll_after_ms=settings.run_event_poll_max_ms
        ),
        accepted=accepted,
        duplicates=duplicates,
        rejected=rejected,
        sample_errors=samples,
    )


@router.get("/transactions", response_model=TransactionListResponse)
async def list_transactions(  # noqa: PLR0913, PLR0917 - FastAPI handler: request + injected deps + filters.
    request: Request,
    tenant: TenantDep,
    session: DbSessionDep,
    limit: Annotated[int, Query(ge=1, le=_MAX_PAGE_LIMIT)] = _DEFAULT_PAGE_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
    risk_band: Annotated[RiskBand | None, Query(alias="riskBand")] = None,
    search: Annotated[str | None, Query(alias="search", max_length=_MAX_SEARCH_LEN)] = None,
) -> TransactionListResponse:
    """Return a keyset page (newest first) + total; optional riskBand + free-text search."""
    repo = _repo(tenant, session)
    rows, next_cursor, total = await repo.page(
        limit=limit, cursor=cursor, risk_band=risk_band, search=search
    )
    await _record_phi_access(
        audit_writer(tenant, session, request),
        actor_id=optional_actor(tenant),
        resource_type="transaction_page",
        resource_id=None,
        count=len(rows),
        source="transaction.list",
    )
    if rows:
        await session.commit()
    summaries = await investigation_summaries(
        session, agency_id=uuid.UUID(tenant.agency_id), transaction_ids=[row.id for row in rows]
    )
    return TransactionListResponse(
        transactions=[_to_response(row, summaries.get(row.id)) for row in rows],
        next_cursor=next_cursor,
        total=total,
    )


@router.get("/transactions/screening", response_model=ScreeningProgress)
async def get_screening_progress(
    tenant: TenantDep,
    session: DbSessionDep,
    settings: SettingsDep,
    job_id: Annotated[uuid.UUID | None, Query(alias="jobId")] = None,
) -> ScreeningProgress:
    """Observe one upload or this tenant's upload backlog without creating work."""
    return await screening_progress(
        session,
        agency_id=uuid.UUID(tenant.agency_id),
        job_id=job_id,
        poll_after_ms=settings.run_event_poll_max_ms,
    )


@router.get("/transactions/{transactionId}", response_model=TransactionResponse)
async def get_transaction(
    transaction_id: Annotated[uuid.UUID, Path(alias="transactionId")],
    request: Request,
    tenant: TenantDep,
    session: DbSessionDep,
) -> TransactionResponse:
    """Return one transaction by id; 404 when missing or owned by another agency."""
    repo = _repo(tenant, session)
    transaction = await repo.get(transaction_id)
    if transaction is None:
        raise AppError("transaction_not_found")
    await _record_phi_access(
        audit_writer(tenant, session, request),
        actor_id=optional_actor(tenant),
        resource_type="transaction",
        resource_id=str(transaction.id),
        count=1,
        source="transaction.detail",
    )
    await session.commit()
    summaries = await investigation_summaries(
        session, agency_id=uuid.UUID(tenant.agency_id), transaction_ids=[transaction.id]
    )
    return _to_response(transaction, summaries.get(transaction.id))
