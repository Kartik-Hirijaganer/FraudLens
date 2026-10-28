"""Summary: Tenant-authorized case projections with frozen rounds and server capabilities.

Key classes:
- (none)

Key functions:
- project_case: enrich an alert summary with current flags and policy capabilities.
- submission_views: read frozen round narrative, rationale, decision and artifact state.
- action_view: project masked append-only activity without fabricating historical identity.

Notes:
- Reads never create identity mappings, labels, contributions or operational history.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import AlertAction, CaseDecision, CaseSubmission
from fraudlens_backend.db.repositories import AuditLogRepository, SarDraftRepository
from fraudlens_backend.db.repositories.alerts import AlertSummaryRow
from fraudlens_backend.models.alerts import AlertActionView, AlertView
from fraudlens_backend.models.case_workflow import ActionCapability, SubmissionView
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.services.case_context import CaseContext
from fraudlens_backend.services.case_policy import action_capabilities
from fraudlens_backend.services.person_identity import resolve_actor
from fraudlens_backend.services.sar_regeneration import sar_draft_to_view


def action_view(action: AlertAction) -> AlertActionView:
    """Keep unknown historical person identities nullable."""
    return AlertActionView(
        action_id=str(action.id),
        action=action.action,
        actor_id=str(action.actor_id),
        actor_person_id=str(action.actor_person_id) if action.actor_person_id else None,
        submission_id=str(action.submission_id) if action.submission_id else None,
        note=action.note,
        from_status=action.from_status,
        to_status=action.to_status,
        created_at=action.created_at,
    )


async def project_case(
    session: AsyncSession,
    *,
    agency_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    row: AlertSummaryRow,
    margin: float,
) -> AlertView:
    """Derive the same contextual policy as commands without acquiring a write lock."""
    alert = row.alert
    ctx = CaseContext(
        session,
        agency_id,
        AuditLogRepository(session, agency_id=agency_id, request_id="case-read"),
        margin,
    )
    ctx.alert = alert
    await ctx.load_evidence()
    capabilities: dict[str, ActionCapability]
    try:
        if actor_id is None:
            raise AppError("person_mapping_required")
        ctx.actor = await resolve_actor(session, agency_id, actor_id)
        capabilities = action_capabilities(ctx.policy())
    except AppError:
        capabilities = {
            key: ActionCapability(allowed=False, reason="Trusted person mapping is required.")
            for key in (
                "pickup",
                "edit",
                "regenerate",
                "submit",
                "dismiss",
                "comment",
                "approve",
                "return",
                "decline",
            )
        }
    latest = (
        await session.execute(
            select(CaseSubmission)
            .where(CaseSubmission.agency_id == agency_id, CaseSubmission.alert_id == alert.id)
            .order_by(CaseSubmission.round.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    decision = (
        None
        if latest is None
        else (
            await session.execute(
                select(CaseDecision.decision).where(
                    CaseDecision.agency_id == agency_id, CaseDecision.submission_id == latest.id
                )
            )
        ).scalar_one_or_none()
    )
    return AlertView(
        alert_id=str(alert.id),
        transaction_id=str(alert.transaction_id),
        run_id=str(alert.run_id),
        origin=alert.origin,
        status=alert.status,
        severity=alert.severity,
        amount=row.amount,
        currency=row.currency,
        assigned_to=str(alert.assigned_to) if alert.assigned_to else None,
        assigned_to_name=row.assigned_to_name,
        owner_person_id=str(alert.owner_person_id) if alert.owner_person_id else None,
        case_version=alert.case_version,
        review_flags=ctx.flags,
        capabilities=capabilities,
        submission_status=decision or ("submitted" if latest else None),
        created_at=alert.created_at,
        updated_at=alert.updated_at,
    )


async def submission_views(
    session: AsyncSession,
    agency_id: uuid.UUID,
    alert_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
) -> list[SubmissionView]:
    """Project exact submitted versions; future working drafts never replace their narrative."""
    rows = (
        (
            await session.execute(
                select(CaseSubmission)
                .where(CaseSubmission.agency_id == agency_id, CaseSubmission.alert_id == alert_id)
                .order_by(CaseSubmission.round.desc())
            )
        )
        .scalars()
        .all()
    )
    can_retry = False
    if actor_id is not None:
        try:
            actor = await resolve_actor(session, agency_id, actor_id)
            can_retry = actor.role.value in {"reviewer", "admin"}
        except AppError:
            pass
    sar = SarDraftRepository(session, agency_id)
    views = []
    for row in rows:
        decision = (
            await session.execute(
                select(CaseDecision).where(
                    CaseDecision.agency_id == agency_id, CaseDecision.submission_id == row.id
                )
            )
        ).scalar_one_or_none()
        action = (
            await session.execute(
                select(AlertAction).where(
                    AlertAction.agency_id == agency_id, AlertAction.id == row.action_id
                )
            )
        ).scalar_one_or_none()
        decision_action = (
            None
            if decision is None
            else (
                await session.execute(
                    select(AlertAction).where(
                        AlertAction.agency_id == agency_id, AlertAction.id == decision.action_id
                    )
                )
            ).scalar_one_or_none()
        )
        draft = await sar.get(row.sar_draft_id) if row.sar_draft_id else None
        views.append(
            SubmissionView(
                submission_id=str(row.id),
                round=row.round,
                maker_account_id=str(row.maker_account_id),
                submitted_by_account_id=str(action.actor_id) if action else None,
                can_retry_pdf=can_retry
                and decision is not None
                and decision.decision == "approve"
                and row.pdf_status != "ready",
                maker_person_id=str(row.maker_person_id),
                contributor_person_ids=row.contributor_person_ids,
                recommendation=row.recommendation,
                rationale=action.note if action else None,
                sar_draft=sar_draft_to_view(draft) if draft else None,
                evidence=row.evidence,
                decision=decision.decision if decision else None,
                reason=decision_action.note if decision_action else None,
                outcome=decision.outcome if decision else None,
                checker_account_id=str(decision.actor_account_id) if decision else None,
                checker_person_id=str(decision.actor_person_id) if decision else None,
                pdf_status=row.pdf_status,
                created_at=row.created_at,
            )
        )
    return views
