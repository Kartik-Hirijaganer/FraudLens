"""Summary: Authoritative transactional maker-checker workflow for HTTP and demo bootstrap.

Key classes:
- AlertWorkflowService: owns pickup, working revisions, immutable submission and atomic decisions.

Key functions:
- (none)

Notes:
- Every command locks the case first and resolves its actor from trusted person records.
- Submission and decision receipts survive later transitions; conflicting key reuse fails.
- Reasons exist only in masked action notes; no compatibility approval or close path remains.
"""

from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import (
    AlertActionType,
    CaseContributor,
    CaseDecision,
    CaseSubmission,
    RagRetrieval,
    SarDraft,
    WorkflowReceipt,
)
from fraudlens_backend.db.models.enums import (
    AlertStatus,
    LabelSource,
    SarQualityStatus,
    SarStatus,
    TrainingLabelType,
)
from fraudlens_backend.db.repositories import AuditLogRepository
from fraudlens_backend.models.case_workflow import (
    DecisionRequest,
    SubmissionRequest,
    WorkflowCommandResult,
    WorkingSarRequest,
)
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.services.case_context import CaseContext
from fraudlens_backend.services.case_policy import enforce_action
from fraudlens_core.phi import mask_text


class AlertWorkflowService:
    """Single tenant's contextual workflow; caller commits or rolls back the complete unit."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        agency_id: uuid.UUID,
        audit: AuditLogRepository,
        low_confidence_margin: float = 0.1,
    ) -> None:
        self.session = session
        self.agency_id = agency_id
        self.audit = audit
        self.margin = low_confidence_margin

    async def context(self, alert_id: uuid.UUID, actor_id: uuid.UUID) -> CaseContext:
        """Resolve tenant, lock and authoritative context before any command."""
        return await CaseContext(self.session, self.agency_id, self.audit, self.margin).load(
            alert_id, actor_id
        )

    async def act(
        self,
        *,
        alert_id: uuid.UUID,
        actor_id: uuid.UUID,
        action: str,
        expected_case_version: int,
        note: str | None = None,
    ) -> None:
        """Retain only self pickup, meaningful comments and policy-controlled dismissal."""
        ctx = await self.context(alert_id, actor_id)
        ctx.expect_version(expected_case_version)
        verb = "pickup" if action == "assign" else action
        if verb not in {"pickup", "comment", "dismiss"}:
            raise AppError("invalid_alert_transition")
        if verb in {"comment", "dismiss"} and not (note and note.strip()):
            raise AppError("workflow_reason_required")
        enforce_action(ctx.policy(), verb)
        ctx.begin()
        if verb == "pickup":
            ctx.alert.assigned_to = actor_id
            ctx.alert.owner_person_id = ctx.actor.person_id
            ctx.alert.status = AlertStatus.IN_REVIEW
        elif verb == "dismiss":
            ctx.alert.status = AlertStatus.DISMISSED
            if ctx.draft is not None:
                ctx.draft.status = SarStatus.WITHDRAWN
            await ctx.label(TrainingLabelType.FALSE_POSITIVE, LabelSource.ANALYST_DISMISS)
        await ctx.action(AlertActionType(action), note)

    async def save_sar(
        self, *, alert_id: uuid.UUID, actor_id: uuid.UUID, payload: WorkingSarRequest
    ) -> SarDraft:
        """Manual authoring and actual narrative changes produce versioned working artifacts."""
        ctx = await self.context(alert_id, actor_id)
        ctx.expect_version(payload.expected_case_version)
        enforce_action(ctx.policy(), "edit")
        current_version = ctx.draft.version if ctx.draft else None
        if current_version != payload.expected_sar_version:
            raise AppError("case_version_conflict")
        masked = mask_text(payload.content)
        if ctx.draft is not None and ctx.draft.content == masked.value:
            return ctx.draft
        ctx.begin()
        if ctx.draft is None:
            draft = SarDraft(
                agency_id=self.agency_id,
                run_id=ctx.alert.run_id,
                alert_id=ctx.alert.id,
                version=1,
                model_id="manual",
                prompt_version="manual",
                prompt_hash=hashlib.sha256(masked.value.encode()).hexdigest(),
                content=masked.value,
                structured={},
                citations=[],
                status=SarStatus.DRAFT,
                quality_status=SarQualityStatus.NOT_RUN,
                created_by=actor_id,
            )
            self.session.add(draft)
            await self.session.flush()
        else:
            draft = await ctx.sar.create_edited_version(
                base=ctx.draft, content=masked.value, created_by=actor_id, alert_id=ctx.alert.id
            )
        ctx.draft = draft
        ctx.flags = ctx.current_flags()
        await ctx.contribute()
        await ctx.action(AlertActionType.EDIT)
        if masked.report.total:
            await self.audit.record(
                actor_id=actor_id,
                action="phi_mask",
                resource_type="sar_draft",
                resource_id=str(draft.id),
                metadata={"maskedCount": str(masked.report.total), "source": "case.narrative"},
            )
        return draft

    async def submit(
        self,
        *,
        alert_id: uuid.UUID,
        actor_id: uuid.UUID,
        payload: SubmissionRequest,
        idempotency_key: str,
    ) -> WorkflowCommandResult:
        """Freeze exact narrative, evidence and accumulated participants in a fresh round."""
        ctx = await self.context(alert_id, actor_id)
        fingerprint = self._fingerprint(actor_id, "submit", payload.model_dump(mode="json"))
        replay = await self._replay(ctx, idempotency_key, fingerprint)
        if replay is not None:
            return replay
        ctx.expect_version(payload.expected_case_version)
        enforce_action(ctx.policy(), "submit")
        if payload.recommendation == "sar" and (
            ctx.draft is None
            or ctx.draft.version != payload.sar_version
            or not ctx.draft.content.strip()
            or ctx.draft.status not in {SarStatus.DRAFT, SarStatus.RETURNED}
        ):
            raise AppError("submitted_sar_required")
        ctx.begin()
        await ctx.contribute()
        contributors = (
            (
                await self.session.execute(
                    select(CaseContributor.person_id)
                    .where(
                        CaseContributor.agency_id == self.agency_id,
                        CaseContributor.alert_id == alert_id,
                    )
                    .order_by(CaseContributor.person_id)
                )
            )
            .scalars()
            .all()
        )
        round_number = (
            await self.session.execute(
                select(func.max(CaseSubmission.round)).where(
                    CaseSubmission.agency_id == self.agency_id, CaseSubmission.alert_id == alert_id
                )
            )
        ).scalar_one() or 0
        retrieval = (
            await self.session.execute(
                select(RagRetrieval).where(
                    RagRetrieval.agency_id == self.agency_id,
                    RagRetrieval.run_id == ctx.alert.run_id,
                )
            )
        ).scalar_one_or_none()
        submission_id = uuid.uuid4()
        ctx.alert.status = AlertStatus.ESCALATED
        entry = await ctx.action(AlertActionType.SUBMIT, payload.rationale, submission_id)
        selected = ctx.draft if payload.recommendation == "sar" else None
        if selected is not None:
            selected.status = SarStatus.SUBMITTED
        if ctx.result is None:
            raise AppError("submitted_sar_required")
        row = CaseSubmission(
            id=submission_id,
            agency_id=self.agency_id,
            alert_id=alert_id,
            round=round_number + 1,
            maker_account_id=ctx.alert.assigned_to,
            maker_person_id=ctx.actor.person_id,
            sar_draft_id=selected.id if selected else None,
            sar_version=selected.version if selected else None,
            recommendation=payload.recommendation,
            contributor_person_ids=[str(person) for person in contributors],
            evidence={
                "runId": str(ctx.alert.run_id),
                "resultId": str(ctx.result.id),
                "retrievalId": str(retrieval.id) if retrieval else None,
                "riskBand": ctx.result.risk_band.value,
                "fraudProbability": ctx.result.fraud_probability,
                "ruleHits": ctx.result.rule_hits,
                "topFeatures": ctx.result.top_features,
                "citations": retrieval.chunks if retrieval else [],
                "reviewFlags": ctx.flags,
            },
            action_id=entry.id,
        )
        self.session.add(row)
        await self.session.flush()
        return await self._receipt(ctx, row, idempotency_key, fingerprint)

    async def decide(
        self,
        *,
        alert_id: uuid.UUID,
        actor_id: uuid.UUID,
        submission_id: uuid.UUID,
        payload: DecisionRequest,
        idempotency_key: str,
    ) -> WorkflowCommandResult:
        """Atomically record decision, masked reason, SAR state, case outcome and label."""
        ctx = await self.context(alert_id, actor_id)
        fingerprint = self._fingerprint(
            actor_id, f"decision:{submission_id}", payload.model_dump(mode="json")
        )
        replay = await self._replay(ctx, idempotency_key, fingerprint)
        if replay is not None:
            return replay
        ctx.expect_version(payload.expected_case_version)
        if ctx.submission is None or ctx.submission.id != submission_id:
            raise AppError("submission_not_active")
        enforce_action(ctx.policy(), payload.decision)
        submission = ctx.submission
        draft = await ctx.sar.get(submission.sar_draft_id) if submission.sar_draft_id else None
        if payload.decision == "approve" and (
            draft is None or draft.status != SarStatus.SUBMITTED or not draft.content.strip()
        ):
            raise AppError("submitted_sar_required")
        ctx.begin()
        if payload.decision == "return":
            ctx.alert.status = AlertStatus.IN_REVIEW
            ctx.alert.assigned_to = submission.maker_account_id
            ctx.alert.owner_person_id = submission.maker_person_id
            if draft is not None:
                draft.status = SarStatus.RETURNED
            revision_base = draft or ctx.draft
            if revision_base is not None:
                await ctx.sar.create_edited_version(
                    base=revision_base,
                    content=revision_base.content,
                    created_by=submission.maker_account_id,
                    alert_id=alert_id,
                )
        else:
            ctx.alert.status = AlertStatus.RESOLVED
            if draft is not None:
                draft.status = (
                    SarStatus.APPROVED if payload.decision == "approve" else SarStatus.REJECTED
                )
                draft.reviewed_by = actor_id
            if payload.outcome is None:
                raise AppError("resolution_label_mismatch")
            await ctx.label(payload.outcome)
            if payload.decision == "approve":
                submission.pdf_status = "pending"
        entry = await ctx.action(AlertActionType(payload.decision), payload.reason, submission.id)
        self.session.add(
            CaseDecision(
                agency_id=self.agency_id,
                submission_id=submission.id,
                actor_account_id=actor_id,
                actor_person_id=ctx.actor.person_id,
                decision=payload.decision,
                outcome=payload.outcome.value if payload.outcome else None,
                action_id=entry.id,
            )
        )
        await self.session.flush()
        return await self._receipt(ctx, submission, idempotency_key, fingerprint, payload.decision)

    @staticmethod
    def _fingerprint(actor_id: uuid.UUID, operation: str, payload: dict[str, object]) -> str:
        """Keep raw command text out of receipts; scope replay to exact actor and operation."""
        return hashlib.sha256(
            json.dumps([str(actor_id), operation, payload], sort_keys=True).encode()
        ).hexdigest()

    async def _replay(
        self, ctx: CaseContext, key: str, fingerprint: str
    ) -> WorkflowCommandResult | None:
        """Replay happens under the case lock before optimistic state checks."""
        if not key.strip():
            raise AppError("workflow_idempotency_required")
        digest = hashlib.sha256(key.encode()).hexdigest()
        row = (
            await self.session.execute(
                select(WorkflowReceipt).where(
                    WorkflowReceipt.agency_id == self.agency_id,
                    WorkflowReceipt.alert_id == ctx.alert.id,
                    WorkflowReceipt.key_digest == digest,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        if row.fingerprint != fingerprint:
            raise AppError("workflow_idempotency_conflict")
        return WorkflowCommandResult.model_validate(row.response)

    async def _receipt(
        self,
        ctx: CaseContext,
        submission: CaseSubmission,
        key: str,
        fingerprint: str,
        decision: str | None = None,
    ) -> WorkflowCommandResult:
        """Persist a frozen response beside the atomic domain writes."""
        result = WorkflowCommandResult(
            alert_id=str(ctx.alert.id),
            submission_id=str(submission.id),
            case_version=ctx.alert.case_version,
            status=ctx.alert.status.value,
            decision=decision,
        )
        self.session.add(
            WorkflowReceipt(
                agency_id=self.agency_id,
                alert_id=ctx.alert.id,
                key_digest=hashlib.sha256(key.encode()).hexdigest(),
                fingerprint=fingerprint,
                response=result.model_dump(mode="json", by_alias=True),
            )
        )
        await self.session.flush()
        return result
