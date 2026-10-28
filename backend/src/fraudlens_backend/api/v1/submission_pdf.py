"""Summary: Tenant-authorized version-bound PDF download and idempotent artifact recovery.

Key classes:
- PdfRetryResponse: truthful persisted PDF readiness after a retry request.

Key functions:
- download_pdf: download only the approved submission's frozen artifact.
- retry_pdf: queue bounded recovery without changing the recorded decision.

Notes:
- Storage keys derive exclusively from authorized tenant and draft identifiers.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Path, Request, Response
from pydantic import Field
from sqlalchemy import select

from fraudlens_backend.api.deps import DbSessionDep, SettingsDep, get_tenant, require_actor
from fraudlens_backend.backends.storage import get_storage_backend
from fraudlens_backend.db.models import CaseDecision, CaseSubmission
from fraudlens_backend.models.common import CamelModel, TenantContext
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.sar.pdf import generate_sar_pdf, sar_pdf_key
from fraudlens_backend.services.case_context import lock_case
from fraudlens_backend.services.person_identity import resolve_actor

router = APIRouter(tags=["alerts"])
TenantDep = Annotated[TenantContext, Depends(get_tenant)]
AlertId = Annotated[uuid.UUID, Path(alias="alertId")]
SubmissionId = Annotated[uuid.UUID, Path(alias="submissionId")]


class PdfRetryResponse(CamelModel):
    """Readiness is independent of the durable approval."""

    pdf_status: str = Field(..., description="Persisted artifact readiness.")


async def _approved(
    session: DbSessionDep, agency_id: uuid.UUID, alert_id: uuid.UUID, submission_id: uuid.UUID
) -> CaseSubmission:
    """Require exact case, tenant and approved round; no cross-tenant existence disclosure."""
    row = (
        await session.execute(
            select(CaseSubmission)
            .join(
                CaseDecision,
                (CaseDecision.submission_id == CaseSubmission.id)
                & (CaseDecision.agency_id == agency_id),
            )
            .where(
                CaseSubmission.agency_id == agency_id,
                CaseSubmission.alert_id == alert_id,
                CaseSubmission.id == submission_id,
                CaseDecision.decision == "approve",
            )
        )
    ).scalar_one_or_none()
    if row is None or row.sar_draft_id is None:
        raise AppError("submission_not_found")
    return row


@router.get("/alerts/{alertId}/submissions/{submissionId}/pdf")
async def download_pdf(
    alert_id: AlertId,
    submission_id: SubmissionId,
    tenant: TenantDep,
    session: DbSessionDep,
    settings: SettingsDep,
) -> Response:
    """Read the complete artifact through authorization instead of exposing a storage URI."""
    agency_id = uuid.UUID(tenant.agency_id)
    row = await _approved(session, agency_id, alert_id, submission_id)
    if row.pdf_status != "ready" or row.sar_draft_id is None:
        raise AppError("submission_pdf_unavailable")
    try:
        content = get_storage_backend(settings).get(sar_pdf_key(agency_id, row.sar_draft_id))
    except Exception as exc:
        raise AppError("submission_pdf_unavailable") from exc
    return Response(
        content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="sar-{submission_id}.pdf"',
            "Cache-Control": "private, no-store",
        },
    )


@router.post(
    "/alerts/{alertId}/submissions/{submissionId}/pdf/retry", response_model=PdfRetryResponse
)
async def retry_pdf(  # noqa: PLR0913, PLR0917 - explicit FastAPI command and injected dependencies.
    alert_id: AlertId,
    submission_id: SubmissionId,
    tenant: TenantDep,
    session: DbSessionDep,
    settings: SettingsDep,
    request: Request,
    background_tasks: BackgroundTasks,
) -> PdfRetryResponse:
    """Reviewer/Admin may retry generation; repeated pending/ready requests are idempotent."""
    agency_id = uuid.UUID(tenant.agency_id)
    await lock_case(session, agency_id, alert_id)
    actor = await resolve_actor(session, agency_id, require_actor(tenant))
    if actor.role.value not in {"reviewer", "admin"}:
        raise AppError("role_permission_required")
    row = await _approved(session, agency_id, alert_id, submission_id)
    # Pending is retried as well: an interrupted process may have lost the deferred task.
    if row.sar_draft_id is None:
        raise AppError("submission_not_found")
    if row.pdf_status != "ready":
        row.pdf_status = "pending"
        await session.commit()
        background_tasks.add_task(
            generate_sar_pdf,
            sessionmaker=request.app.state.db_sessionmaker,
            storage=get_storage_backend(settings),
            agency_id=agency_id,
            draft_id=row.sar_draft_id,
            max_attempts=settings.sar_pdf_max_attempts,
        )
    return PdfRetryResponse(pdf_status=row.pdf_status)
