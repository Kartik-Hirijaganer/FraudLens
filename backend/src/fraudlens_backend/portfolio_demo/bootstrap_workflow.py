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
from typing import Any, Literal, cast

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import (
    AgentExecution,
    Alert,
    AlertAction,
    AlertStatus,
    AnalysisResult,
    AnalysisRun,
    AnalysisRunEvent,
    CaseContributor,
    CaseDecision,
    CaseSubmission,
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
    WorkflowReceipt,
)
from fraudlens_backend.db.models.enums import TrainingLabelType
from fraudlens_backend.db.repositories import (
    AlertRepository,
    AuditLogRepository,
    SarDraftRepository,
)
from fraudlens_backend.jobs.runner import run_batch_score
from fraudlens_backend.models.case_workflow import DecisionRequest, SubmissionRequest
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
    PIPELINE_RAISED_STATUSES,
)
from fraudlens_backend.services.alert_workflow import AlertWorkflowService
from fraudlens_backend.settings import AppSettings

_RESET_ORDER: tuple[type[Any], ...] = (
    WorkflowReceipt,
    CaseDecision,
    CaseSubmission,
    CaseContributor,
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


async def apply_workflow_targets(
    session: AsyncSession, config: PortfolioDemoConfig, audit: AuditLogRepository
) -> tuple[int, int]:
    """Build a possible story exclusively through the same policy used by HTTP commands."""
    workflow = AlertWorkflowService(
        session,
        agency_id=config.agency.id,
        audit=audit,
        low_confidence_margin=config.probe.low_confidence_margin,
    )
    alerts = AlertRepository(session, config.agency.id)
    sar_repo = SarDraftRepository(session, config.agency.id)
    maker_id = config.persona(config.workflow.assignee).seed_user_id
    reviewer_id = config.persona(config.workflow.sar_review_actor).seed_user_id
    alert_moves = sar_moves = 0
    for scenario in config.scenarios:
        if scenario.alert_target is None or scenario.sar_target is None:
            continue
        run_id = await _run_id_for(session, config, scenario)
        alert = None if run_id is None else await alerts.get_for_run(run_id)
        if alert is None:
            raise BootstrapRefusedError(f"scenario '{scenario.scenario_id}' has no pipeline alert")
        draft = await sar_repo.get_for_run(alert.run_id)
        if draft is None or (
            draft.status is SarStatus.FAILED and scenario.sar_target is not SarStatus.FAILED
        ):
            raise BootstrapRefusedError(
                f"scenario '{scenario.scenario_id}' has no usable SAR: "
                "the quality gate rejected it at every cascade tier; restore its evidence"
            )
        if alert.status == scenario.alert_target and draft.status == scenario.sar_target:
            continue
        if scenario.alert_target in PIPELINE_RAISED_STATUSES:
            raise BootstrapRefusedError(
                f"scenario '{scenario.scenario_id}' pipeline state differs from configured target"
            )
        if alert.assigned_to is None:
            await workflow.act(
                alert_id=alert.id,
                actor_id=maker_id,
                action="assign",
                expected_case_version=alert.case_version,
            )
            alert_moves += 1
        if scenario.alert_target is AlertStatus.IN_REVIEW and not scenario.returned:
            continue
        submission = await workflow.submit(
            alert_id=alert.id,
            actor_id=maker_id,
            payload=SubmissionRequest(
                expected_case_version=alert.case_version,
                rationale=config.workflow.resolution_note,
                recommendation="sar",
                sar_version=draft.version,
            ),
            idempotency_key=f"bootstrap:{scenario.scenario_id}:submit",
        )
        alert_moves += 1
        if scenario.alert_target is AlertStatus.ESCALATED:
            continue
        decision: Literal["approve", "return", "decline"] = (
            "return"
            if scenario.returned
            else "approve"
            if scenario.sar_target is SarStatus.APPROVED
            else "decline"
        )
        reason = (
            config.workflow.approval_note
            if decision == "approve"
            else config.workflow.rejection_note
        )
        outcome = (
            None
            if decision == "return"
            else TrainingLabelType.CONFIRMED_FRAUD
            if decision == "approve"
            else TrainingLabelType.BENIGN
        )
        await workflow.decide(
            alert_id=alert.id,
            actor_id=reviewer_id,
            submission_id=uuid.UUID(submission.submission_id),
            payload=DecisionRequest(
                expected_case_version=alert.case_version,
                decision=decision,
                reason=reason,
                outcome=outcome,
            ),
            idempotency_key=f"bootstrap:{scenario.scenario_id}:decision",
        )
        alert_moves += 1
        sar_moves += 1
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
