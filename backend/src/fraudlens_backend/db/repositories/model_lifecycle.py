"""Summary: The platform model-lifecycle repository + its pure transition helpers (plan §5.4,
§10.5, §16 Phase 10). The model registry/deployment are PLATFORM tables (models are global, not
tenant-scoped, ADR-015), so — like `ModelRegistryRepository` — this is NOT a
`TenantScopedRepository`; it owns the human-gated WRITES the read-only registry repo deliberately
omits. It drives the lifecycle state machine over `model_versions.status` + the single
`model_deployments` pointer: candidate → (passing eval) shadow → (human) approve → canary
5/25/50% → 100% active → rollback, flipping the pointer IN PLACE so running processes reload on
the next run with no redeploy (plan §5.4). It also reads the hash-only `model_inference_logs` to
compute the canary-vs-active deviation the auto-abort guard acts on (plan §10.5.1). The pure
module-level helpers (`can_shadow`/`can_approve`/`can_canary`/`canary_should_abort`) carry no IO so
the gating rules are unit-testable in isolation (mirrors `alerts.next_alert_status`).

Key classes:
- LabelCounts: matured-label totals (overall + fraud-positive + fraud-negative) for eligibility.
- CanaryStats: per-arm inference counts + mean predicted probability (active vs canary).
- RollbackOutcome: what a rollback did (aborted a canary, or restored the previous active).
- ModelLifecycleRepository: platform-scoped lifecycle writes + canary inference stats.

Key functions:
- labels_eligible: the §9.4 retrain-eligibility rule (total + per-class matured-label thresholds).
- can_shadow: candidate → shadow is legal only with a passing evaluation (plan §10.5.1).
- can_approve: approve is legal only from shadow (human gate after eval+shadow, plan §5.4).
- can_canary: canary is legal only from an approved shadow / an in-progress canary.
- canary_should_abort: the §10.5.1 auto-abort rule (deviation over the min-sample window).

Notes:
- `activate` archives the outgoing active and records it as `previous_active_version_id`, so a
  later rollback restores exactly the model that was live (last-known-good, plan §10.6).
- `rollback` is dual-purpose: it ABORTS an in-progress canary if one is set (the §10.5.1 bad-canary
  path), else it RESTORES the previous active pointer — and raises `nothing_to_rollback` when
  neither exists, so the API surfaces a 409 rather than silently no-op'ing.
- `canary_inference_stats` reads only `fraud_probability` keyed by model version id (hash-only, no
  PHI), aggregated across agencies because the lifecycle decision is global (tenant-safe, §9.2).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import (
    DriftReport,
    JobStatus,
    ModelDeployment,
    ModelEvaluation,
    ModelInferenceLog,
    ModelTrainingRun,
    ModelVersion,
    ModelVersionStatus,
    TrainingLabel,
    TrainingLabelType,
)

# Canary statuses a version may be promoted/updated through (approved shadow or live canary).
_CANARY_SOURCE_STATUSES: frozenset[ModelVersionStatus] = frozenset(
    {ModelVersionStatus.SHADOW, ModelVersionStatus.CANARY}
)
_IN_PROGRESS_JOB_STATUSES: frozenset[JobStatus] = frozenset({JobStatus.PENDING, JobStatus.RUNNING})


# A matured label counts toward the fraud-positive target when it confirms fraud the model should
# have caught (confirmed fraud + a missed false-negative), and the fraud-negative target otherwise
# (benign + a false-positive the model over-flagged) — the two classes a balanced retrain needs.
_POSITIVE_LABELS: frozenset[TrainingLabelType] = frozenset(
    {TrainingLabelType.CONFIRMED_FRAUD, TrainingLabelType.FALSE_NEGATIVE}
)


@dataclass(frozen=True)
class LabelCounts:
    """Matured reviewed-label totals used to gate retrain eligibility (plan §9.4)."""

    total: int
    positives: int
    negatives: int


def labels_eligible(counts: LabelCounts, *, min_total: int, min_per_class: int) -> bool:
    """True when matured labels clear the total AND per-class thresholds for a retrain (§9.4)."""
    return (
        counts.total >= min_total
        and counts.positives >= min_per_class
        and counts.negatives >= min_per_class
    )


@dataclass(frozen=True)
class CanaryStats:
    """Per-arm inference counts + mean predicted probability for the active vs canary models."""

    active_count: int
    active_mean: float
    canary_count: int
    canary_mean: float


@dataclass(frozen=True)
class RollbackOutcome:
    """What a rollback did: the action taken and the version label now serving as active."""

    action: str
    active_version_label: str


def can_shadow(status: ModelVersionStatus, *, has_passing_evaluation: bool) -> bool:
    """True when a candidate may move to shadow — only with a passing evaluation (plan §10.5.1)."""
    return status == ModelVersionStatus.CANDIDATE and has_passing_evaluation


def can_approve(status: ModelVersionStatus) -> bool:
    """True when a version may be approved — only from shadow (human gate, plan §5.4)."""
    return status == ModelVersionStatus.SHADOW


def can_canary(status: ModelVersionStatus, *, approved: bool) -> bool:
    """True when a version may (continue to) canary — an approved shadow or a live canary."""
    return approved and status in _CANARY_SOURCE_STATUSES


def canary_should_abort(stats: CanaryStats, *, min_samples: int, max_deviation: float) -> bool:
    """Return whether the canary deviates enough to auto-abort (plan §10.5.1).

    The guard acts only once both arms have at least `min_samples` inferences (the min-sample
    window); it then aborts when the absolute gap between the canary's and active's mean predicted
    probability (the alert-rate / precision proxy) exceeds `max_deviation`.
    """
    if stats.active_count < min_samples or stats.canary_count < min_samples:
        return False
    return abs(stats.canary_mean - stats.active_mean) > max_deviation


class ModelLifecycleRepository:
    """Platform-scoped model-lifecycle writes + canary inference stats (no agency scope)."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the session; the registry/deployment are platform-global (ADR-015)."""
        self._session = session

    async def list_training_runs(
        self, *, limit: int = 50, offset: int = 0
    ) -> Sequence[ModelTrainingRun]:
        """Return training runs newest-first (the whole history is small)."""
        stmt = (
            select(ModelTrainingRun)
            .order_by(ModelTrainingRun.created_at.desc(), ModelTrainingRun.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def training_in_progress(self) -> bool:
        """True when a training run is pending/running (the 409 guard for a new trigger)."""
        stmt = select(func.count()).where(ModelTrainingRun.status.in_(_IN_PROGRESS_JOB_STATUSES))
        return bool((await self._session.execute(stmt)).scalar_one())

    async def matured_label_counts(self, *, as_of: datetime) -> LabelCounts:
        """Count matured reviewed labels (matured_at <= as_of) by target class, across all agencies.

        Training is GLOBAL over tenant labels (ADR-015): the aggregate counts (never per-agency,
        never PHI) decide whether a retrain is eligible (plan §9.4). Immature/unmatured labels
        (matured_at NULL or in the future) are excluded.
        """
        stmt = (
            select(TrainingLabel.label, func.count())
            .where(TrainingLabel.matured_at.is_not(None), TrainingLabel.matured_at <= as_of)
            .group_by(TrainingLabel.label)
        )
        by_label = {row[0]: int(row[1]) for row in await self._session.execute(stmt)}
        positives = sum(by_label.get(label, 0) for label in _POSITIVE_LABELS)
        total = sum(by_label.values())
        return LabelCounts(total=total, positives=positives, negatives=total - positives)

    async def list_drift_reports(
        self, *, limit: int = 50, offset: int = 0
    ) -> Sequence[DriftReport]:
        """Return advisory drift reports newest-first (plan §10.5; advisory-only)."""
        stmt = (
            select(DriftReport)
            .order_by(DriftReport.created_at.desc(), DriftReport.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def has_passing_evaluation(self, version_id: uuid.UUID) -> bool:
        """True when the version has at least one passing `model_evaluations` row (plan §10.5.1)."""
        stmt = select(func.count()).where(
            ModelEvaluation.model_version_id == version_id,
            ModelEvaluation.passed.is_(True),
        )
        return bool((await self._session.execute(stmt)).scalar_one())

    async def get_deployment(self) -> ModelDeployment | None:
        """Return the single live deployment pointer row, or None when unset."""
        return (await self._session.execute(select(ModelDeployment).limit(1))).scalar_one_or_none()

    async def promote_to_shadow(self, version: ModelVersion) -> None:
        """Move a candidate to shadow (log-only; serves no traffic until canary)."""
        version.status = ModelVersionStatus.SHADOW
        await self._session.flush()

    async def approve(self, version: ModelVersion, *, approved_by: uuid.UUID) -> None:
        """Stamp the human approval on a shadow version (status stays shadow until canary)."""
        version.approved_by = approved_by
        version.approved_at = datetime.now(UTC)
        await self._session.flush()

    async def start_canary(
        self, version: ModelVersion, *, percent: int, updated_by: uuid.UUID | None
    ) -> None:
        """Begin/continue a canary rollout: mark the version canary + point the deployment at it."""
        deployment = await self.get_deployment()
        if deployment is None:
            return
        version.status = ModelVersionStatus.CANARY
        deployment.canary_version_id = version.id
        deployment.canary_percent = percent
        deployment.updated_by = updated_by
        await self._session.flush()

    async def activate(self, version: ModelVersion, *, updated_by: uuid.UUID | None) -> None:
        """Promote a version to active (canary 100%): flip the pointer + archive the outgoing."""
        deployment = await self.get_deployment()
        if deployment is None:
            return
        outgoing = await self._session.get(ModelVersion, deployment.active_version_id)
        if outgoing is not None and outgoing.id != version.id:
            outgoing.status = ModelVersionStatus.ARCHIVED
            deployment.previous_active_version_id = outgoing.id
        version.status = ModelVersionStatus.ACTIVE
        deployment.active_version_id = version.id
        deployment.canary_version_id = None
        deployment.canary_percent = 0
        deployment.updated_by = updated_by
        await self._session.flush()

    async def rollback(self, *, updated_by: uuid.UUID | None) -> RollbackOutcome | None:
        """Abort an in-progress canary, else restore the previous active pointer (plan §10.5/§10.6).

        Returns None when there is nothing to roll back (the API maps that to a 409).
        """
        deployment = await self.get_deployment()
        if deployment is None:
            return None
        if deployment.canary_version_id is not None:
            canary = await self._session.get(ModelVersion, deployment.canary_version_id)
            if canary is not None:
                canary.status = ModelVersionStatus.ARCHIVED
            deployment.canary_version_id = None
            deployment.canary_percent = 0
            deployment.updated_by = updated_by
            await self._session.flush()
            active = await self._session.get(ModelVersion, deployment.active_version_id)
            return RollbackOutcome(
                action="canary_aborted",
                active_version_label=active.version_label if active is not None else "",
            )
        if deployment.previous_active_version_id is not None:
            outgoing = await self._session.get(ModelVersion, deployment.active_version_id)
            if outgoing is not None:
                outgoing.status = ModelVersionStatus.ARCHIVED
            restored = await self._session.get(ModelVersion, deployment.previous_active_version_id)
            if restored is not None:
                restored.status = ModelVersionStatus.ACTIVE
            deployment.active_version_id = deployment.previous_active_version_id
            deployment.previous_active_version_id = None
            deployment.updated_by = updated_by
            await self._session.flush()
            return RollbackOutcome(
                action="restored_previous",
                active_version_label=restored.version_label if restored is not None else "",
            )
        return None

    async def inference_probabilities(
        self, version_id: uuid.UUID, *, limit: int = 10_000
    ) -> list[float]:
        """Return a version's inference fraud probabilities oldest-first (hash-only, no PHI).

        Backs the advisory drift scan (plan §10.5); aggregated across agencies because the model is
        global (ADR-015), and it reads only the probability — never a feature value or identifier.
        """
        stmt = (
            select(ModelInferenceLog.fraud_probability)
            .where(ModelInferenceLog.model_version_id == version_id)
            .order_by(ModelInferenceLog.created_at.asc(), ModelInferenceLog.id.asc())
            .limit(limit)
        )
        return [float(value) for value in (await self._session.execute(stmt)).scalars().all()]

    async def canary_inference_stats(self, deployment: ModelDeployment) -> CanaryStats:
        """Aggregate per-arm inference counts + mean probability for the active vs canary models."""
        version_ids = [deployment.active_version_id]
        if deployment.canary_version_id is not None:
            version_ids.append(deployment.canary_version_id)
        stmt = (
            select(
                ModelInferenceLog.model_version_id,
                func.count().label("n"),
                func.avg(ModelInferenceLog.fraud_probability).label("mean"),
            )
            .where(ModelInferenceLog.model_version_id.in_(version_ids))
            .group_by(ModelInferenceLog.model_version_id)
        )
        rows = {
            row[0]: (int(row[1]), float(row[2] or 0.0)) for row in await self._session.execute(stmt)
        }
        active = rows.get(deployment.active_version_id, (0, 0.0))
        canary = (
            rows.get(deployment.canary_version_id, (0, 0.0))
            if deployment.canary_version_id is not None
            else (0, 0.0)
        )
        return CanaryStats(
            active_count=active[0],
            active_mean=active[1],
            canary_count=canary[0],
            canary_mean=canary[1],
        )
