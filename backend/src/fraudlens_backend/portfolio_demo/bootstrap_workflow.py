"""Summary: Portfolio-demo scoring, workflow transitions, job recording, and reset operations.

Key classes:
- (none)

Key functions:
- score_pending: score only configured rows without completed runs.
- apply_workflow_targets: drive configured alert and SAR transitions.
- story_job_id: derive the stable bootstrap job identifier.
- record_job:
- reset_story: remove only tenant-owned operational story rows.

Notes:
- All writes remain agency-scoped and use the shared workflow state machine.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import (
    AgentExecution,
    Alert,
    AlertAction,
    AlertActionType,
    AlertStatus,
    AnalysisResult,
    AnalysisRun,
    AnalysisRunEvent,
    JobExecution,
    JobStatus,
    JobType,
    ModelInferenceLog,
    RagRetrieval,
    SarDraft,
    SarGenerationAttempt,
    SarStatus,
    TrainingLabel,
    Transaction,
)
from fraudlens_backend.db.repositories import (
    AlertRepository,
    AuditLogRepository,
    SarDraftRepository,
)
from fraudlens_backend.db.repositories.alerts import next_alert_status
from fraudlens_backend.jobs.runner import run_batch_score
from fraudlens_backend.models.alerts import SarReviewDecision
from fraudlens_backend.pipeline_wiring import PipelineComponents
from fraudlens_backend.portfolio_demo.bootstrap_guards import (
    BootstrapRefusedError,
    BootstrapSummary,
)
from fraudlens_backend.portfolio_demo.config import (
    AUDIT_ACTION,
    PortfolioDemoConfig,
    PortfolioDemoScenario,
)
from fraudlens_backend.portfolio_demo.verification import (
    DECIDED_SAR_STATES,
    PIPELINE_RAISED_STATUSES,
)
from fraudlens_backend.services.alert_workflow import (
    AlertActionCommand,
    AlertWorkflowService,
    SarReviewCommand,
)
from fraudlens_backend.settings import AppSettings

_TERMINAL_ACTIONS: frozenset[AlertActionType] = frozenset(
    {AlertActionType.RESOLVE, AlertActionType.DISMISS}
)
_SAR_DECISIONS: dict[SarStatus, SarReviewDecision] = {
    SarStatus.APPROVED: SarReviewDecision.APPROVE,
    SarStatus.REJECTED: SarReviewDecision.REJECT,
}
_RESET_ORDER: tuple[type[Any], ...] = (
    AlertAction,
    SarGenerationAttempt,
    SarDraft,
    Alert,
    AnalysisResult,
    RagRetrieval,
    AnalysisRunEvent,
    AgentExecution,
    TrainingLabel,
    ModelInferenceLog,
    AnalysisRun,
    Transaction,
)


async def score_pending(
    session: AsyncSession,
    config: PortfolioDemoConfig,
    settings: AppSettings,
    components: PipelineComponents,
    transaction_ids: dict[str, uuid.UUID],
) -> tuple[int, int]:
    """Batch-score the `score: true` rows that have no run yet; return (scored, already_scored).

    Ids are passed explicitly in configured order — never `select_uninvestigated`, which would
    sweep the rows deliberately held unscored for a visitor to investigate live.
    """
    pending: list[uuid.UUID] = []
    already = 0
    for scenario in config.scored_scenarios:
        transaction_id = transaction_ids[scenario.scenario_id]
        transaction = await session.get(Transaction, transaction_id)
        if transaction is not None and transaction.latest_run_id is not None:
            already += 1
            continue
        pending.append(transaction_id)
    if pending:
        await run_batch_score(
            session=session,
            components=components,
            settings=settings,
            agency_id=config.agency.id,
            transaction_ids=pending,
        )
    return len(pending), already


def _action_for_target(current: AlertStatus, target: AlertStatus) -> AlertActionType | None:
    """Return the action whose legal transition reaches `target`, derived from the state machine."""
    for action in AlertActionType:
        if action is AlertActionType.COMMENT:
            continue  # comment never changes status, so it can never reach a different target
        if next_alert_status(current, action) is target:
            return action
    return None


async def _apply_sar_target(  # noqa: PLR0913 - the draft, its alert, and the actor stay explicit.
    workflow: AlertWorkflowService,
    config: PortfolioDemoConfig,
    *,
    alert: Alert,
    draft: SarDraft,
    target: SarStatus,
    actor_id: uuid.UUID,
) -> tuple[bool, str | None]:
    """Apply one configured SAR target; return (applied, the configured note for the decision)."""
    if draft.status is target:
        return False, None
    decision = _SAR_DECISIONS.get(target) if target in DECIDED_SAR_STATES else None
    if decision is None:
        raise BootstrapRefusedError(
            f"SAR draft for alert '{alert.id}' is '{draft.status.value}' but the story targets "
            f"'{target.value}', which no review decision produces"
        )
    await workflow.review_sar(
        SarReviewCommand(alert_id=alert.id, actor_id=actor_id, decision=decision)
    )
    notes = {
        SarReviewDecision.APPROVE: config.workflow.approval_note,
        SarReviewDecision.REJECT: config.workflow.rejection_note,
    }
    return True, notes[decision]


async def _apply_alert_target(
    workflow: AlertWorkflowService,
    config: PortfolioDemoConfig,
    *,
    alert: Alert,
    target: AlertStatus,
    assignee_id: uuid.UUID,
) -> bool:
    """Apply one configured alert target through the shared service; return whether it moved."""
    if alert.status is target:
        return False
    if target in PIPELINE_RAISED_STATUSES:
        raise BootstrapRefusedError(
            f"alert '{alert.id}' is '{alert.status.value}' but the story targets '{target.value}', "
            "which only the pipeline's own alert-raise produces"
        )
    action = _action_for_target(alert.status, target)
    if action is None:
        raise BootstrapRefusedError(
            f"no legal action moves alert '{alert.id}' from '{alert.status.value}' to "
            f"'{target.value}'"
        )
    note = config.workflow.resolution_note if action in _TERMINAL_ACTIONS else None
    actor_key = (
        config.workflow.resolution_actor
        if action in _TERMINAL_ACTIONS
        else config.workflow.assignment_actor
    )
    await workflow.apply_action(
        AlertActionCommand(
            alert_id=alert.id,
            actor_id=config.persona(actor_key).seed_user_id,
            action=action,
            assignee_id=assignee_id if action is AlertActionType.ASSIGN else None,
            note=note,
        )
    )
    return True


async def apply_workflow_targets(
    session: AsyncSession, config: PortfolioDemoConfig, audit: AuditLogRepository
) -> tuple[int, int]:
    """Apply every configured alert/SAR target; return (alert transitions, SAR transitions)."""
    workflow = AlertWorkflowService(session, agency_id=config.agency.id, audit=audit)
    alerts = AlertRepository(session, config.agency.id)
    sar_repo = SarDraftRepository(session, config.agency.id)
    reviewer_id = config.persona(config.workflow.sar_review_actor).seed_user_id
    assignee_id = config.persona(config.workflow.assignee).seed_user_id
    alert_moves = sar_moves = 0
    for scenario in config.scenarios:
        if scenario.alert_target is None or scenario.sar_target is None:
            continue
        run_id = await _run_id_for(session, config, scenario)
        alert = None if run_id is None else await alerts.get_for_run(run_id)
        if alert is None:
            raise BootstrapRefusedError(
                f"scenario '{scenario.scenario_id}' targets an alert the pipeline did not raise"
            )
        draft = await sar_repo.get_for_run(alert.run_id)
        if draft is None:
            raise BootstrapRefusedError(
                f"scenario '{scenario.scenario_id}' targets a SAR the pipeline did not draft"
            )
        # Named here rather than discovered two transitions later. A gate-rejected draft is also
        # what stamps `sar_unavailable` on the alert and raises it `pending_review`, so without
        # this the run refuses on the ALERT status and reports a cause that is only a symptom.
        if draft.status is SarStatus.FAILED and scenario.sar_target is not SarStatus.FAILED:
            raise BootstrapRefusedError(
                f"scenario '{scenario.scenario_id}' targets a '{scenario.sar_target.value}' SAR "
                "but the draft is 'failed': the quality gate rejected it at every cascade tier. "
                "Restore the evidence the gate requires rather than re-targeting the story"
            )
        applied, note = await _apply_sar_target(
            workflow,
            config,
            alert=alert,
            draft=draft,
            target=scenario.sar_target,
            actor_id=reviewer_id,
        )
        if applied:
            sar_moves += 1
            if note is not None:
                # The SAR review path persists no free text, so the configured reason is recorded
                # as a comment: auditable, and `comment` cannot change the alert's status.
                await workflow.apply_action(
                    AlertActionCommand(
                        alert_id=alert.id,
                        actor_id=reviewer_id,
                        action=AlertActionType.COMMENT,
                        note=note,
                    )
                )
        if await _apply_alert_target(
            workflow,
            config,
            alert=alert,
            target=scenario.alert_target,
            assignee_id=assignee_id,
        ):
            alert_moves += 1
    return alert_moves, sar_moves


async def _run_id_for(
    session: AsyncSession, config: PortfolioDemoConfig, scenario: PortfolioDemoScenario
) -> uuid.UUID | None:
    """Return the latest run id for a scenario's transaction, or None when it was never scored."""
    stmt = select(Transaction.latest_run_id).where(
        Transaction.agency_id == config.agency.id,
        Transaction.external_id == config.external_id(scenario),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# --------------------------------------------------------------------------------------------------
# The story's own job_executions row
# --------------------------------------------------------------------------------------------------


def story_job_id(config: PortfolioDemoConfig) -> uuid.UUID:
    """Return the stable job id derived from the story identity (never a literal)."""
    return uuid.uuid5(uuid.NAMESPACE_OID, config.story_identity)


async def record_job(
    session: AsyncSession, config: PortfolioDemoConfig, summary: BootstrapSummary
) -> None:
    """Upsert the story's single `job_executions` row, incrementing attempts on a re-run."""
    payload: dict[str, Any] = {
        "storyIdentity": config.story_identity,
        "storyVersion": config.story_version,
        "schemaVersion": config.schema_version,
        "modelVersionLabel": config.model.version_label,
        "featureSpecVersion": config.model.feature_spec_version,
    }
    result = summary.model_dump(mode="json")
    job = await session.get(JobExecution, story_job_id(config))
    if job is None:
        session.add(
            JobExecution(
                id=story_job_id(config),
                agency_id=config.agency.id,
                job_type=JobType.SEED,
                status=JobStatus.SUCCEEDED,
                payload=payload,
                result=result,
                attempts=1,
            )
        )
    else:
        job.status = JobStatus.SUCCEEDED
        job.payload = payload
        job.result = result
        job.attempts = job.attempts + 1
    await session.flush()


# --------------------------------------------------------------------------------------------------
# Reset
# --------------------------------------------------------------------------------------------------


async def reset_story(
    session: AsyncSession, config: PortfolioDemoConfig, audit: AuditLogRepository
) -> dict[str, int]:
    """Delete only the tenant's OPERATIONAL rows in FK order and audit the aggregate (no commit).

    The agency, its users and identities, rules, the model registry, job history, and audit logs all
    survive — a reset returns the story to its pre-bootstrap baseline, it does not un-provision the
    tenant. The caller re-runs the ensure path afterwards.
    """
    deleted: dict[str, int] = {}
    for model in _RESET_ORDER:
        outcome = cast(
            CursorResult[Any],
            await session.execute(delete(model).where(model.agency_id == config.agency.id)),
        )
        deleted[str(model.__tablename__)] = int(outcome.rowcount or 0)
    await audit.record(
        actor_id=None,
        action=AUDIT_ACTION,
        resource_type="portfolio_demo_story",
        resource_id=config.story_identity,
        metadata={"step": "reset", **{table: str(count) for table, count in deleted.items()}},
    )
    await session.flush()
    return deleted
