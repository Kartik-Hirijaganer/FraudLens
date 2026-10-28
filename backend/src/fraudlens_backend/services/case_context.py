"""Summary: Tenant-scoped locked case loading and common atomic workflow writes.

Key classes:
- CaseContext: database collaborators and authoritative workflow facts for one case.

Key functions:
- lock_case: acquire the common PostgreSQL case-row lock with a fresh ORM snapshot.

Notes:
- All mutation paths acquire this lock before reading the latest SAR or submission.
- Services flush only; the API or trusted bootstrap owns commit/rollback.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import (
    Alert,
    AlertAction,
    AnalysisResult,
    AnalysisRun,
    CaseContributor,
    CaseDecision,
    CaseSubmission,
    SarDraft,
)
from fraudlens_backend.db.models.enums import (
    AlertActionType,
    AlertStatus,
    LabelSource,
    RunStatus,
    SarStatus,
    TrainingLabelType,
)
from fraudlens_backend.db.repositories import (
    AlertRepository,
    AuditLogRepository,
    SarDraftRepository,
)
from fraudlens_backend.db.repositories.alerts import compute_review_flags, load_label_maturity_days
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.services.case_policy import CasePolicy
from fraudlens_backend.services.person_identity import WorkflowActor, resolve_actor
from fraudlens_core.phi import mask_text


async def lock_case(session: AsyncSession, agency_id: uuid.UUID, alert_id: uuid.UUID) -> Alert:
    """Lock and refresh the tenant-scoped row; stale identity-map values cannot bypass fences."""
    alert = (
        await session.execute(
            select(Alert)
            .where(Alert.agency_id == agency_id, Alert.id == alert_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if alert is None:
        raise AppError("alert_not_found")
    return alert


class CaseContext:
    """One transaction's authoritative case, identity, evidence and active submission."""

    def __init__(
        self, session: AsyncSession, agency_id: uuid.UUID, audit: AuditLogRepository, margin: float
    ) -> None:
        self.session = session
        self.agency_id = agency_id
        self.audit = audit
        self.margin = margin
        self.sar = SarDraftRepository(session, agency_id)
        self.alerts = AlertRepository(session, agency_id)
        self.alert: Alert
        self.actor: WorkflowActor
        self.draft: SarDraft | None = None
        self.submission: CaseSubmission | None = None
        self.run: AnalysisRun | None = None
        self.result: AnalysisResult | None = None
        self.flags: list[dict[str, str]] = []

    async def load(self, alert_id: uuid.UUID, actor_id: uuid.UUID) -> CaseContext:
        """Lock case, resolve trusted identity, then load its current authoritative evidence."""
        self.alert = await lock_case(self.session, self.agency_id, alert_id)
        self.actor = await resolve_actor(self.session, self.agency_id, actor_id)
        await self.load_evidence()
        return self

    async def load_evidence(self) -> None:
        """Read evidence and frozen active round after the case lock is held."""
        self.draft = await self.sar.get_for_run(self.alert.run_id)
        self.run = (
            await self.session.execute(
                select(AnalysisRun).where(
                    AnalysisRun.agency_id == self.agency_id, AnalysisRun.id == self.alert.run_id
                )
            )
        ).scalar_one_or_none()
        self.result = (
            await self.session.execute(
                select(AnalysisResult).where(
                    AnalysisResult.agency_id == self.agency_id,
                    AnalysisResult.run_id == self.alert.run_id,
                )
            )
        ).scalar_one_or_none()
        self.submission = (
            await self.session.execute(
                select(CaseSubmission)
                .outerjoin(
                    CaseDecision,
                    (CaseDecision.submission_id == CaseSubmission.id)
                    & (CaseDecision.agency_id == self.agency_id),
                )
                .where(
                    CaseSubmission.agency_id == self.agency_id,
                    CaseSubmission.alert_id == self.alert.id,
                    CaseDecision.id.is_(None),
                )
                .order_by(CaseSubmission.round.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        self.flags = self.current_flags()

    def current_flags(self) -> list[dict[str, str]]:
        """Recompute flags; historical flags never substitute for current persisted evidence."""
        if self.result is None:
            return [
                {
                    "flag": "evidence_unavailable",
                    "reason": "Completed machine evidence is required.",
                }
            ]
        return compute_review_flags(
            risk_band=self.result.risk_band,
            fraud_probability=self.result.fraud_probability,
            sar_status=self.draft.status.value
            if self.draft is not None and self.draft.content.strip()
            else SarStatus.FAILED.value,
            low_confidence_margin=self.margin,
        )

    def policy(self) -> CasePolicy:
        """Build contextual policy with frozen preparation participants for active checking."""
        return CasePolicy(
            mapped=True,
            role=self.actor.role.value,
            status=self.alert.status,
            unassigned=self.alert.assigned_to is None,
            owner=self.alert.owner_person_id == self.actor.person_id,
            preparer=self.submission is not None
            and str(self.actor.person_id) in self.submission.contributor_person_ids,
            active_submission=self.submission is not None,
            stable=self.run is not None
            and self.run.status in {RunStatus.COMPLETED, RunStatus.FAILED}
            and self.result is not None,
            flagged=bool(self.flags),
            usable_sar=self.submission is not None
            and self.submission.sar_draft_id is not None
            and self.submission.recommendation == "sar",
        )

    def expect_version(self, version: int) -> None:
        """Optimistic fence checked only after idempotent replay and the database lock."""
        if self.alert.case_version != version:
            raise AppError("case_version_conflict")

    async def contribute(self) -> None:
        """Carry participation through every revision and regeneration; never delete it."""
        exists = (
            await self.session.execute(
                select(CaseContributor.id).where(
                    CaseContributor.agency_id == self.agency_id,
                    CaseContributor.alert_id == self.alert.id,
                    CaseContributor.person_id == self.actor.person_id,
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            self.session.add(
                CaseContributor(
                    agency_id=self.agency_id,
                    alert_id=self.alert.id,
                    person_id=self.actor.person_id,
                    account_id=self.actor.account_id,
                )
            )
            await self.session.flush()

    async def action(
        self,
        action: AlertActionType,
        note: str | None = None,
        submission_id: uuid.UUID | None = None,
    ) -> AlertAction:
        """Store a reason exactly once; audit contains identifiers and transition information."""
        masked = mask_text(note) if note is not None else None
        row = AlertAction(
            agency_id=self.agency_id,
            alert_id=self.alert.id,
            actor_id=self.actor.account_id,
            actor_person_id=self.actor.person_id,
            submission_id=submission_id,
            action=action,
            note=masked.value if masked else None,
            from_status=self.before_status.value,
            to_status=self.alert.status.value,
        )
        self.session.add(row)
        self.alert.case_version += 1
        self.alert.review_flags = self.flags
        await self.session.flush()
        metadata = {
            "fromStatus": row.from_status or "",
            "toStatus": row.to_status or "",
            "actorPersonId": str(self.actor.person_id),
            "caseVersion": str(self.alert.case_version),
        }
        if submission_id is not None:
            metadata["submissionId"] = str(submission_id)
        await self.audit.record(
            actor_id=self.actor.account_id,
            action=f"case.{action.value}",
            resource_type="alert",
            resource_id=str(self.alert.id),
            metadata=metadata,
        )
        if masked and masked.report.total:
            await self.audit.record(
                actor_id=self.actor.account_id,
                action="phi_mask",
                resource_type="alert",
                resource_id=str(self.alert.id),
                metadata={
                    "maskedCount": str(masked.report.total),
                    "source": "case.action",
                    "categories": ",".join(
                        f"{key}:{value}" for key, value in masked.report.categories.items()
                    ),
                },
            )
        await self.session.refresh(self.alert)
        return row

    async def label(
        self, outcome: TrainingLabelType, source: LabelSource = LabelSource.ANALYST_REVIEW
    ) -> uuid.UUID:
        """Write the terminal training label in the same transaction as decision and reason."""
        days = await load_label_maturity_days(self.session)
        row = await self.alerts.create_training_label(
            transaction_id=self.alert.transaction_id,
            run_id=self.alert.run_id,
            label=outcome,
            source=source,
            created_by=self.actor.account_id,
            matured_at=datetime.now(UTC) + timedelta(days=days),
        )
        return row.id

    @property
    def before_status(self) -> AlertStatus:
        """Status captured before one command mutates this context."""
        return self._before_status

    def begin(self) -> None:
        """Capture transition source once, immediately before domain writes."""
        self._before_status = self.alert.status
