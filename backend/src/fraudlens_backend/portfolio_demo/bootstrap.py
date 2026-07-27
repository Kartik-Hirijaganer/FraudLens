"""Summary: The idempotent portfolio-demo bootstrap (plan §16 Phase 6) — the logic behind the thin
`scripts/bootstrap_portfolio_demo.py` CLI, kept in the backend package so it is importable and
testable. Nothing about the story is decided here: every id, payload, actor, note, and expected
count comes from `config/portfolio-demo.yaml`. Before any write it runs the full preflight —
validate the config, take the story's Postgres advisory lock, confirm the DB agency IS the
configured tenant, refuse when any other persistent agency exists, require `portfolio_demo_enabled`
in prod, and verify the pinned bundle (manifest sidecar, feature-spec version, checksum-by-loading).
It then detects operational state on the derived external-id namespace, resolves the four-way
model-state matrix, ingests the authored rows (failing on feature-hash drift), scores only the
`score: true` rows through the real `run_batch_score` pipeline, applies the configured alert/SAR
targets through the shared `AlertWorkflowService`, verifies the result against `expected`, and
upserts ONE `job_executions` row whose attempts increment on re-run. Bands, alerts, and SAR drafts
are only ever produced by the pipeline — never written directly.

Key classes:
- BootstrapRefusedError: a preflight/guard refusal carrying a PHI-free reason (exit non-zero).
- OperationalState: what the tenant currently holds (empty / story rows only / foreign rows).
- ModelPromoter: injected protocol for `activate_model.py`'s register+promote chain.
- BootstrapSummary: the PHI-free aggregate recorded on the story's `job_executions` row.

Key functions:
- detect_operational_state: classify the tenant by whether it holds only configured story rows.
- acquire_story_lock: take the story identity's Postgres advisory lock (no-op off Postgres).
- assert_configured_tenant: the DB agency IS the configured one and no other agency persists.
- assert_enabled_in_prod: prod requires the explicit feature gate (fails closed by default).
- assert_execution_modes: the runtime LLM/RAG modes are the ones the story was calibrated for.
- verify_model_bundle: load the pinned bundle and check label, sidecar, feature spec, checksum.
- story_job_id: the stable `job_executions` id derived from the story identity.
- preflight: run every guard in order and return the detected operational state.
- ensure_active_model: resolve the four-way model-state matrix (verify / promote / fail).
- apply_story: run the whole bootstrap and return its summary (raises on any delta).
- reset_story: delete only the tenant's OPERATIONAL rows in FK order, then rebuild.

Notes:
- The advisory lock is Postgres-only; on SQLite (tests) it degrades to a no-op, because a single
  in-memory connection cannot race. The key derives from the story identity, so a `story_version`
  bump re-keys it along with the external ids and the audit request id.
- The resolve action carries NO training label: an outcome label is a human fraud judgement the
  story does not declare, and fabricating one would be the same sin as writing a band directly.
- SAR review decisions record their configured note as a `comment` alert action, because the SAR
  review path persists no free text of its own and an approved/rejected SAR with no recorded reason
  is exactly the un-auditable state the governance rules forbid. `comment` never changes status, so
  no pinned `alert_states` count can move.
- The alert action for a target is DERIVED from `next_alert_status` rather than mapped by hand, so
  the state machine stays the single owner of legal transitions.
- The `job_executions` row reuses `JobType.SEED` (the bootstrap seeds the story exactly as
  `_record_seed_job` seeds the foundation) at an id derived from the story identity, so it upserts
  instead of appending and no enum/migration surface changes.
- Nothing logged or recorded here contains a transaction payload, account, credential, or note.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import (
    Agency,
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
    SarStatus,
    TrainingLabel,
    Transaction,
)
from fraudlens_backend.db.repositories import (
    AlertRepository,
    AuditLogRepository,
    ModelRegistryRepository,
    SarDraftRepository,
)
from fraudlens_backend.db.repositories.alerts import next_alert_status
from fraudlens_backend.db.repositories.model_registry import FIXTURE_MODEL_LABEL
from fraudlens_backend.jobs.runner import run_batch_score
from fraudlens_backend.models.alerts import SarReviewDecision
from fraudlens_backend.pipeline_wiring import PipelineComponents
from fraudlens_backend.portfolio_demo.config import (
    AUDIT_ACTION,
    PortfolioDemoConfig,
    PortfolioDemoScenario,
)
from fraudlens_backend.portfolio_demo.ingest import ensure_story_transactions
from fraudlens_backend.portfolio_demo.verification import (
    DECIDED_SAR_STATES,
    PIPELINE_RAISED_STATUSES,
    VerificationReport,
    format_deltas,
    verify_story,
)
from fraudlens_backend.services.alert_workflow import (
    AlertActionCommand,
    AlertWorkflowService,
    SarReviewCommand,
)
from fraudlens_backend.settings import AppSettings

# Artifact-bundle layout the pinned model must present (the sidecar `activate_model` requires too).
_MANIFEST_SIDECAR = "manifest.json"
_METADATA_SIDECAR = "metadata.json"

# Postgres is the only dialect with advisory locks; SQLite tests degrade to a no-op.
_POSTGRES_DIALECT = "postgresql"

# Actions that close an alert — the only ones the configured resolution note is recorded on.
_TERMINAL_ACTIONS: frozenset[AlertActionType] = frozenset(
    {AlertActionType.RESOLVE, AlertActionType.DISMISS}
)

# The review decision that reaches each decided SAR status (the SAR lifecycle's own mapping).
_SAR_DECISIONS: dict[SarStatus, SarReviewDecision] = {
    SarStatus.APPROVED: SarReviewDecision.APPROVE,
    SarStatus.REJECTED: SarReviewDecision.REJECT,
}

# The tenant's OPERATIONAL rows, in a delete order that respects every foreign key. This order is a
# SCHEMA invariant (verified against `db/models/`), not a deployment value, so it lives in code.
# Agency, users, rules, model registry, job history, and audit logs are deliberately NOT here.
# Typed loosely because the loop below uses each model's `agency_id` column and `__tablename__`,
# which the declarative base does not expose on the CLASS object.
_RESET_ORDER: tuple[type[Any], ...] = (
    AlertAction,
    SarDraft,
    Alert,
    AnalysisResult,
    RagRetrieval,
    AnalysisRunEvent,
    TrainingLabel,
    ModelInferenceLog,
    AnalysisRun,
    Transaction,
)


class BootstrapRefusedError(RuntimeError):
    """Raised when a guard refuses to proceed; the message is a PHI-free operator reason."""


class OperationalState(StrEnum):
    """What the configured tenant currently holds, judged by the derived external-id namespace."""

    EMPTY = "empty"
    STORY = "story"
    FOREIGN = "foreign"


class ModelPromoter(Protocol):
    """Registers + promotes the configured bundle to ACTIVE (`activate_model.py`'s chain).

    Injected rather than imported: the promotion chain lives in `scripts/`, which is not on the
    backend's import path, so the CLI supplies it and tests can supply the same function.
    """

    def __call__(
        self, session: AsyncSession, *, version_label: str
    ) -> Awaitable[str]:  # pragma: no cover - structural type
        """Promote `version_label` to ACTIVE and return a PHI-free outcome summary."""
        ...


class BootstrapSummary(BaseModel):
    """The PHI-free aggregate the story's `job_executions` row records."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    story_version: str = Field(..., description="Story revision that was applied.")
    model_version_label: str = Field(..., description="Model label the story is pinned to.")
    model_outcome: str = Field(..., description="What the model-state matrix decided.")
    transactions_created: int = Field(..., ge=0, description="Authored rows newly inserted.")
    transactions_existing: int = Field(..., ge=0, description="Authored rows already present.")
    scored: int = Field(..., ge=0, description="Rows handed to the batch scorer this run.")
    already_scored: int = Field(..., ge=0, description="Rows skipped because a run already exists.")
    alert_transitions: int = Field(..., ge=0, description="Configured alert transitions applied.")
    sar_transitions: int = Field(..., ge=0, description="Configured SAR decisions applied.")
    verified: bool = Field(..., description="Whether verification matched the configured story.")


# --------------------------------------------------------------------------------------------------
# Preflight guards — every one runs BEFORE any write
# --------------------------------------------------------------------------------------------------


async def acquire_story_lock(session: AsyncSession, config: PortfolioDemoConfig) -> None:
    """Take the story identity's Postgres advisory lock; refuse when another run holds it."""
    bind = session.get_bind()
    if bind.dialect.name != _POSTGRES_DIALECT:
        return
    held = (
        await session.execute(select(func.pg_try_advisory_lock(config.advisory_lock_key)))
    ).scalar_one()
    if not held:
        raise BootstrapRefusedError(
            "another portfolio demo bootstrap holds this story's advisory lock — retry once it ends"
        )


async def assert_configured_tenant(session: AsyncSession, config: PortfolioDemoConfig) -> None:
    """Confirm the DB agency IS the configured tenant and that no other agency persists."""
    agency = await session.get(Agency, config.agency.id)
    if agency is None:
        raise BootstrapRefusedError(
            "the configured portfolio demo agency does not exist — run the foundation seed first"
        )
    if agency.name != config.agency.name or agency.slug != config.agency.slug:
        raise BootstrapRefusedError(
            "the stored agency does not match the configured name/slug — reconcile the seed first"
        )
    others = (
        await session.execute(
            select(func.count()).select_from(Agency).where(Agency.id != agency.id)
        )
    ).scalar_one()
    if others:
        raise BootstrapRefusedError(
            f"{others} other agency row(s) exist; the portfolio demo owns exactly one tenant"
        )


def assert_enabled_in_prod(settings: AppSettings) -> None:
    """In prod the feature gate must be explicitly on; the Python default fails closed."""
    if settings.environment == "prod" and not settings.portfolio_demo_enabled:
        raise BootstrapRefusedError(
            "portfolio demo mode is disabled — set portfolio_demo_enabled to bootstrap in prod"
        )


def assert_execution_modes(config: PortfolioDemoConfig, settings: AppSettings) -> None:
    """Confirm the runtime provider modes are the deterministic ones the story assumes.

    `execution.llm_mode` / `execution.rag_embedding_mode` exist so the pinned distribution names the
    providers it was calibrated against; running the bootstrap under different ones would produce
    SAR narratives and retrievals the story never verified.
    """
    mismatches = [
        f"{field} is '{actual}' but the story assumes '{expected}'"
        for field, actual, expected in (
            ("llm_mode", settings.llm_mode, config.execution.llm_mode),
            (
                "rag_embedding_mode",
                settings.rag_embedding_mode,
                config.execution.rag_embedding_mode,
            ),
        )
        if actual != expected
    ]
    if mismatches:
        raise BootstrapRefusedError("; ".join(mismatches))


def verify_model_bundle(config: PortfolioDemoConfig, models_dir: Path) -> None:
    """Verify the pinned bundle's presence, sidecars, feature spec, and booster checksum."""
    from fraudlens_ml.scoring import ArtifactError, load_artifact  # noqa: PLC0415 - heavy import

    bundle = models_dir / config.model.version_label
    for sidecar in (_METADATA_SIDECAR, _MANIFEST_SIDECAR):
        if not (bundle / sidecar).is_file():
            raise BootstrapRefusedError(
                f"the pinned model bundle is missing its '{sidecar}' sidecar — train or fetch it"
            )
    try:
        loaded = load_artifact(bundle)  # loading re-verifies the booster checksum
    except ArtifactError as exc:
        raise BootstrapRefusedError(f"the pinned model bundle is unusable: {exc}") from exc
    if loaded.version_label != config.model.version_label:
        raise BootstrapRefusedError(
            "the pinned bundle's version label does not match the configuration"
        )
    if loaded.feature_spec.version != config.model.feature_spec_version:
        raise BootstrapRefusedError(
            "the pinned bundle's feature-spec version does not match the configuration"
        )


async def detect_operational_state(
    session: AsyncSession, config: PortfolioDemoConfig
) -> OperationalState:
    """Classify the tenant: empty, only configured story rows, or holding foreign rows."""
    stored = set(
        (
            await session.execute(
                select(Transaction.external_id).where(Transaction.agency_id == config.agency.id)
            )
        )
        .scalars()
        .all()
    )
    if not stored:
        return OperationalState.EMPTY
    configured = {config.external_id(scenario) for scenario in config.scenarios}
    return OperationalState.STORY if stored <= configured else OperationalState.FOREIGN


# --------------------------------------------------------------------------------------------------
# Model-state matrix
# --------------------------------------------------------------------------------------------------


async def ensure_active_model(
    session: AsyncSession,
    config: PortfolioDemoConfig,
    *,
    promote: ModelPromoter,
    audit: AuditLogRepository,
) -> str:
    """Resolve the four-way model-state matrix and return a PHI-free outcome summary.

    Configured model already active ⇒ verify only. No active model ⇒ register + promote. The
    seeded fixture active ⇒ promote the configured model and audit the flip. Any OTHER non-fixture
    model active ⇒ refuse: silently displacing an operator's promotion is not the bootstrap's call.
    """
    label = config.model.version_label
    pointer = await ModelRegistryRepository(session).build_pointer()
    if pointer is not None and pointer.active_version_label == label:
        return "configured model already active"
    if pointer is not None and pointer.active_version_label != FIXTURE_MODEL_LABEL:
        raise BootstrapRefusedError(
            f"a different model is active ('{pointer.active_version_label}'); the portfolio demo "
            f"will not displace it — activate '{label}' deliberately instead"
        )
    outcome = await promote(session, version_label=label)
    await audit.record(
        actor_id=None,
        action=AUDIT_ACTION,
        resource_type="model_version",
        resource_id=label,
        metadata={
            "step": "activate_model",
            "from": FIXTURE_MODEL_LABEL if pointer is not None else "none",
            "to": label,
        },
    )
    return outcome


# --------------------------------------------------------------------------------------------------
# Scoring + the configured workflow targets
# --------------------------------------------------------------------------------------------------


async def _score_pending(
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


async def _apply_workflow_targets(
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


async def _record_job(
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


# --------------------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------------------


async def preflight(
    session: AsyncSession,
    config: PortfolioDemoConfig,
    settings: AppSettings,
    *,
    models_dir: Path,
    reset: bool,
) -> OperationalState:
    """Run every guard in order and return the detected operational state (no writes)."""
    await acquire_story_lock(session, config)
    await assert_configured_tenant(session, config)
    assert_enabled_in_prod(settings)
    assert_execution_modes(config, settings)
    verify_model_bundle(config, models_dir)
    state = await detect_operational_state(session, config)
    if state is OperationalState.FOREIGN and not reset:
        raise BootstrapRefusedError(
            "the tenant holds rows outside the configured story (visitor-created or unknown); "
            "re-run with --reset to rebuild the pinned baseline"
        )
    return state


async def apply_story(  # noqa: PLR0913 - injected collaborators keep the orchestration testable.
    session: AsyncSession,
    config: PortfolioDemoConfig,
    settings: AppSettings,
    *,
    components: PipelineComponents,
    models_dir: Path,
    promote: ModelPromoter,
    reset: bool = False,
) -> tuple[BootstrapSummary, VerificationReport]:
    """Bootstrap (or resume) the configured story and verify it; raise on any delta."""
    audit = AuditLogRepository(
        session, agency_id=config.agency.id, request_id=config.audit_request_id
    )
    await preflight(session, config, settings, models_dir=models_dir, reset=reset)
    if reset:
        await reset_story(session, config, audit)
        await session.commit()
    model_outcome = await ensure_active_model(session, config, promote=promote, audit=audit)
    await session.commit()

    ingest = await ensure_story_transactions(session, config)
    await session.commit()
    scored, already_scored = await _score_pending(
        session, config, settings, components, ingest.transaction_ids
    )
    alert_moves, sar_moves = await _apply_workflow_targets(session, config, audit)
    await session.commit()

    report = await verify_story(session, config)
    summary = BootstrapSummary(
        story_version=config.story_version,
        model_version_label=config.model.version_label,
        model_outcome=model_outcome,
        transactions_created=ingest.created,
        transactions_existing=ingest.existing,
        scored=scored,
        already_scored=already_scored,
        alert_transitions=alert_moves,
        sar_transitions=sar_moves,
        verified=report.ok,
    )
    await _record_job(session, config, summary)
    await session.commit()
    if not report.ok:
        raise BootstrapRefusedError(
            "the applied story does not match its configured expectations — "
            f"{format_deltas(report.deltas)}"
        )
    return summary, report
