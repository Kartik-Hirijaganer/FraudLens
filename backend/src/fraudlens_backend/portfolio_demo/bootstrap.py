"""Summary: Stable orchestration facade for applying the configured portfolio demo story.

Key classes:
- (none)

Key functions:
- preflight: execute every no-write story guard.
- apply_story: apply, verify, and record the configured story.

Notes:
- Guard and workflow symbols remain available through explicit re-exports.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.repositories import AuditLogRepository
from fraudlens_backend.pipeline_wiring import PipelineComponents
from fraudlens_backend.portfolio_demo.bootstrap_guards import (
    BootstrapRefusedError,
    BootstrapSummary,
    ModelPromoter,
    OperationalState,
    acquire_story_lock,
    assert_configured_tenant,
    assert_enabled_in_prod,
    assert_execution_modes,
    assert_rag_index,
    detect_operational_state,
    ensure_active_model,
    verify_model_bundle,
)
from fraudlens_backend.portfolio_demo.bootstrap_workflow import (
    apply_workflow_targets,
    record_job,
    reset_story,
    score_pending,
    story_job_id,
)
from fraudlens_backend.portfolio_demo.config import PortfolioDemoConfig
from fraudlens_backend.portfolio_demo.ingest import ensure_story_transactions
from fraudlens_backend.portfolio_demo.verification import (
    VerificationReport,
    format_deltas,
    verify_story,
)
from fraudlens_backend.settings import AppSettings

__all__ = [
    "BootstrapRefusedError",
    "BootstrapSummary",
    "ModelPromoter",
    "OperationalState",
    "acquire_story_lock",
    "apply_story",
    "assert_configured_tenant",
    "assert_enabled_in_prod",
    "assert_execution_modes",
    "assert_rag_index",
    "detect_operational_state",
    "ensure_active_model",
    "preflight",
    "reset_story",
    "story_job_id",
    "verify_model_bundle",
]


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
    assert_rag_index(settings)
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
    scored, already_scored = await score_pending(
        session, config, settings, components, ingest.transaction_ids
    )
    alert_moves, sar_moves = await apply_workflow_targets(session, config, audit)
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
    await record_job(session, config, summary)
    await session.commit()
    if not report.ok:
        raise BootstrapRefusedError(
            "the applied story does not match its configured expectations — "
            f"{format_deltas(report.deltas)}"
        )
    return summary, report
