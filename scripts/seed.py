"""Summary: Idempotent dev/demo seed (plan §9.3). Re-runnable safely — every entity is
upserted by its natural key (or a fixed id), so `make db-seed` (and `make local-demo`) can
run repeatedly without creating duplicates. It seeds the foundation the schema can hold: the
demo agency + its auditor/analyst/reviewer/admin users (shared with the auth dev-bypass via
fraudlens_backend.demo), the default global `system_config` tunables, the **active fixture
model** (a training dataset → training run → ACTIVE model version → the single deployment
pointer), the curated synthetic IEEE-CIS transactions (masked at ingest via the shared importer)
so `make local-demo` shows real, listable transactions, and — added in Phase 10 — a balanced set
of **pre-matured reviewed `training_labels`** (each backed by a completed run) so `make retrain`
is immediately eligible for the lifecycle demo (§9.4). It also seeds a PHI-free set of
**alerts across the lifecycle** (an open triage queue + in-review/escalated/resolved/dismissed),
each backed by a completed run with canonical `compute_review_flags`, plus attached **SAR drafts**
(some filed/approved), so the analyst dashboard renders populated locally instead of all-zero. The
run itself is recorded in `job_executions`. The six global baseline AML rules (`agency_id`
NULL) come from the one canonical `fraudlens_core.DEFAULT_RULE_DEFINITIONS` (Phase 4). RAG,
runs, and real model artifacts are seeded by their own phases, which extend this script.
Refuses to run when `environment == "prod"`.

Key classes:
- SeedSummary: counts of the demo entities the seed ensured exist.

Key functions:
- seed: idempotently upsert the demo dataset into a session (no commit).
- main: build the engine from settings, run the seed in a transaction (dev/demo only).

Notes:
- Fixed UUIDs (agency, fixture dataset/run/version/deployment, seed job) make the seed
  deterministic and idempotent across runs and easy to assert in tests.
- The fixture model carries only feature NAMES + a content hash — no PHI, no agency_id as a
  feature (ADR-015); its ACTIVE pointer resolves to the committed real XGBoost+SHAP artifact
  bundle (`make train-model --fixture`), so local-demo scores via a real model (Phase 5).
- Transactions are ingested through `import_ieee.seed_sample_transactions`, i.e. the same
  masked-only path as the API, so the seed never persists raw account identifiers (ADR-014).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import (
    Agency,
    Alert,
    AlertAction,
    AlertActionType,
    AlertStatus,
    AmlRule,
    AnalysisRun,
    JobExecution,
    JobStatus,
    JobType,
    LabelSource,
    ModelDeployment,
    ModelTrainingRun,
    ModelTrigger,
    ModelVersion,
    ModelVersionStatus,
    RunStatus,
    SarDraft,
    SarStatus,
    Severity,
    SystemConfig,
    TrainingDataset,
    TrainingLabel,
    TrainingLabelType,
    Transaction,
    User,
    UserRole,
)
from fraudlens_backend.db.repositories.alerts import compute_review_flags
from fraudlens_backend.db.session import build_sessionmaker, create_engine_from_settings
from fraudlens_backend.demo import (
    DEMO_AGENCY_ID,
    DEMO_AGENCY_NAME,
    DEMO_AGENCY_SLUG,
    DEMO_USER_ID,
    DEMO_USERS,
)
from fraudlens_backend.settings import AppSettings, get_settings
from fraudlens_core import DEFAULT_RULE_DEFINITIONS, RiskBand
from fraudlens_ml.scoring import ModelGates, current_feature_spec
from import_ieee import seed_sample_transactions

REPO_ROOT = Path(__file__).resolve().parents[1]

# Fixed ids so the seed is deterministic + idempotent (re-runs update, never duplicate).
_SEED_JOB_ID = uuid.UUID("22222222-2222-4222-8222-000000000001")
_FIXTURE_DATASET_ID = uuid.UUID("22222222-2222-4222-8222-000000000002")
_FIXTURE_TRAINING_RUN_ID = uuid.UUID("22222222-2222-4222-8222-000000000003")
_FIXTURE_MODEL_VERSION_ID = uuid.UUID("22222222-2222-4222-8222-000000000004")
_FIXTURE_DEPLOYMENT_ID = uuid.UUID("22222222-2222-4222-8222-000000000005")
_FIXTURE_MODEL_LABEL = "v0-fixture"

# The fixture's artifact uri (relative to settings.model_artifacts_dir = data/models) — the
# committed Phase 5 bundle the scorer's active pointer resolves to (`make train-model --fixture`).
_FIXTURE_ARTIFACT_URI = _FIXTURE_MODEL_LABEL

# Pre-matured reviewed labels (+ their completed runs) seeded so `make retrain` is immediately
# eligible in local-demo (plan §9.4 / §16 Phase 10 acceptance). A balanced set over the first
# transactions, matured in the past to stand in for already-reviewed-and-matured decisions. The
# cycle yields equal fraud-positive (confirmed_fraud/false_negative) and fraud-negative
# (benign/false_positive) targets so the per-class eligibility gate clears.
_DEMO_LABEL_CYCLE: tuple[TrainingLabelType, ...] = (
    TrainingLabelType.CONFIRMED_FRAUD,
    TrainingLabelType.BENIGN,
    TrainingLabelType.FALSE_NEGATIVE,
    TrainingLabelType.FALSE_POSITIVE,
)
_DEMO_LABEL_COUNT = 12
_DEMO_LABEL_MATURED_DAYS_AGO = 1

# Tenant-safe fixture feature spec: feature NAMES only — never PHI or agency_id (ADR-015).
# Sourced from the one canonical fraudlens-ml feature spec so the seeded fixture row matches
# the committed artifact the scorer loads (no duplication, rule 5).
_FIXTURE_FEATURE_SPEC: dict[str, Any] = current_feature_spec().model_dump()

# Default GLOBAL (agency_id NULL) runtime tunables (plan §9.1 `system_config` keys). The
# model-promotion gates are the canonical §10.5.1 ModelGates defaults (no duplicated thresholds).
_DEFAULT_CONFIG: dict[str, Any] = {
    "riskBandThresholds": {"low": 0.0, "medium": 0.3, "high": 0.6, "critical": 0.85},
    "alertThreshold": 0.6,
    "llmDailyBudgetUsd": 5.0,
    "llmSessionBudgetUsd": 0.5,
    "defaultModelId": _FIXTURE_MODEL_LABEL,
    "retentionDays": 365,
    "labelMaturityDays": 7,
    "canaryPercent": 0,
    "modelGates": ModelGates().model_dump(by_alias=True),
    "featureFlags": {"phiNerMasking": False},
}

# --- Synthetic open-alert queue + SAR drafts (dev/demo only) ---------------------------------
# A deterministic, PHI-free set of alerts across the lifecycle so `make local-demo` shows a
# populated dashboard (an open triage queue, in-review/escalated load, and filed SARs) rather than
# an all-zero one. Each alert is backed by a completed analysis run over a seeded transaction, and
# its force-review flags come from the ONE canonical `compute_review_flags` (rule 5), so seeded
# rows read exactly like alerts the pipeline raises in production.
_LOW_CONFIDENCE_MARGIN = 0.1
_DEMO_SAR_PROMPT_VERSION = "sar-v1"
_DEMO_SAR_CONTENT = (
    "Synthetic demo SAR narrative (local demo only): a structuring pattern of rapid, "
    "sub-threshold transfers to a newly onboarded counterparty. Contains no real PHI."
)
_DEMO_SAR_CITATIONS: list[Any] = [
    {
        "citation": "31 CFR 1020.320",
        "title": "Reports by banks of suspicious transactions",
        "source": "FinCEN",
        "snippet": "A bank shall file a SAR for a transaction it knows or suspects is suspicious.",
    }
]

# Model risk signal per severity → (risk band, probability). The probabilities are chosen so
# `compute_review_flags` yields varied, realistic flags: critical band flags mandatory review, and
# near-0.5 probabilities flag low model confidence.
_SEVERITY_SIGNAL: dict[Severity, tuple[RiskBand, float]] = {
    Severity.CRITICAL: (RiskBand.CRITICAL, 0.93),
    Severity.HIGH: (RiskBand.HIGH, 0.55),
    Severity.MEDIUM: (RiskBand.MEDIUM, 0.50),
    Severity.LOW: (RiskBand.LOW, 0.16),
}

# Alert lifecycle plan — (status, severity, count, sar_status | None) — expanded in order into
# individual alerts. Open alerts are listed first, so they get the freshest timestamps and lead the
# risk-then-recency queue on the dashboard.
_ALERT_PLAN: tuple[tuple[AlertStatus, Severity, int, SarStatus | None], ...] = (
    (AlertStatus.OPEN, Severity.CRITICAL, 2, SarStatus.DRAFT),
    (AlertStatus.OPEN, Severity.HIGH, 3, SarStatus.DRAFT),
    (AlertStatus.OPEN, Severity.MEDIUM, 6, None),
    (AlertStatus.OPEN, Severity.LOW, 5, None),
    (AlertStatus.IN_REVIEW, Severity.HIGH, 2, SarStatus.REVIEWED),
    (AlertStatus.IN_REVIEW, Severity.MEDIUM, 2, SarStatus.REVIEWED),
    (AlertStatus.ESCALATED, Severity.CRITICAL, 1, SarStatus.APPROVED),
    (AlertStatus.ESCALATED, Severity.HIGH, 2, SarStatus.APPROVED),
    (AlertStatus.RESOLVED, Severity.HIGH, 1, SarStatus.APPROVED),
    (AlertStatus.RESOLVED, Severity.MEDIUM, 2, SarStatus.APPROVED),
    (AlertStatus.RESOLVED, Severity.LOW, 1, SarStatus.APPROVED),
    (AlertStatus.DISMISSED, Severity.MEDIUM, 1, None),
    (AlertStatus.DISMISSED, Severity.LOW, 1, None),
)
_ASSIGNED_STATUSES = frozenset({AlertStatus.IN_REVIEW, AlertStatus.ESCALATED})
_REVIEWED_SAR_STATUSES = frozenset({SarStatus.REVIEWED, SarStatus.APPROVED, SarStatus.REJECTED})
_ACTION_FOR_STATUS: dict[AlertStatus, AlertActionType] = {
    AlertStatus.IN_REVIEW: AlertActionType.ASSIGN,
    AlertStatus.ESCALATED: AlertActionType.ESCALATE,
    AlertStatus.RESOLVED: AlertActionType.RESOLVE,
    AlertStatus.DISMISSED: AlertActionType.DISMISS,
}
_FIRST_ALERT_MINUTES_AGO = 2
_ALERT_SPACING_MINUTES = 6
# The demo reviewer (matched by role, not a duplicated id) reviews/approves the seeded SARs.
_DEMO_REVIEWER_ID = next(spec.user_id for spec in DEMO_USERS if spec.role.value == "reviewer")


def _fixture_metrics() -> dict[str, Any]:
    """Read the committed fixture bundle's holdout metrics so the seeded row matches reality."""
    metadata_path = REPO_ROOT / "data" / "models" / _FIXTURE_ARTIFACT_URI / "metadata.json"
    if metadata_path.is_file():
        return dict(json.loads(metadata_path.read_text("utf-8")).get("metrics", {}))
    return {}


class SeedSummary(BaseModel):
    """Counts of the demo entities the seed ensured exist (target state, not inserts)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    agencies: int = Field(..., description="Demo agencies ensured (always 1).")
    users: int = Field(..., description="Demo users ensured.")
    config_keys: int = Field(..., description="Global system_config keys ensured.")
    rules: int = Field(..., description="Global baseline AML rules ensured (6).")
    model_versions: int = Field(..., description="Fixture model versions ensured (1).")
    deployments: int = Field(..., description="Model deployment pointers ensured (1).")
    transactions: int = Field(..., description="Synthetic IEEE-CIS transactions ensured.")
    training_labels: int = Field(..., description="Pre-matured demo training labels ensured.")
    alerts: int = Field(..., description="Synthetic demo alerts ensured across the lifecycle.")
    sar_drafts: int = Field(..., description="Demo SAR drafts ensured (some filed/approved).")


def _feature_spec_hash(spec: dict[str, Any]) -> str:
    """Return a deterministic content hash of the fixture feature spec (reproducibility)."""
    canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _ensure_agency(session: AsyncSession) -> None:
    """Insert the demo agency at its fixed id if absent."""
    if await session.get(Agency, DEMO_AGENCY_ID) is None:
        session.add(Agency(id=DEMO_AGENCY_ID, name=DEMO_AGENCY_NAME, slug=DEMO_AGENCY_SLUG))


async def _ensure_users(session: AsyncSession) -> int:
    """Insert each demo user (by agency_id + email) if absent; return the demo user count."""
    for spec in DEMO_USERS:
        stmt = select(User).where(User.agency_id == DEMO_AGENCY_ID, User.email == spec.email)
        if (await session.execute(stmt)).scalar_one_or_none() is None:
            session.add(
                User(
                    id=spec.user_id,
                    agency_id=DEMO_AGENCY_ID,
                    email=spec.email,
                    display_name=spec.display_name,
                    role=spec.role,
                )
            )
    return len(DEMO_USERS)


async def _ensure_bootstrap_admin(session: AsyncSession, settings: AppSettings) -> int:
    """Upsert the optional dashboard-created first admin into public.users.

    The admin must already exist in Supabase Auth; these settings only reconcile the app-owned
    row so token `sub` (auth.users.id) resolves to a tenant user. When unset, the local demo seed
    remains unchanged and returns zero extra users.
    """
    if not settings.bootstrap_admin_user_id and not settings.bootstrap_admin_email:
        return 0
    if not settings.bootstrap_admin_user_id or not settings.bootstrap_admin_email:
        raise RuntimeError("bootstrap admin requires both user id and email")
    try:
        user_id = uuid.UUID(settings.bootstrap_admin_user_id)
    except ValueError as exc:
        raise RuntimeError("bootstrap admin user id must be a UUID") from exc
    user = await session.get(User, user_id)
    if user is None:
        session.add(
            User(
                id=user_id,
                agency_id=DEMO_AGENCY_ID,
                email=settings.bootstrap_admin_email,
                display_name=settings.bootstrap_admin_display_name,
                role=UserRole.ADMIN,
            )
        )
    else:
        user.agency_id = DEMO_AGENCY_ID
        user.email = settings.bootstrap_admin_email
        user.display_name = settings.bootstrap_admin_display_name
        user.role = UserRole.ADMIN
    return 1


async def _ensure_config(session: AsyncSession) -> int:
    """Insert each default GLOBAL config key (agency_id NULL) if absent; return the count."""
    for key, value in _DEFAULT_CONFIG.items():
        stmt = select(SystemConfig).where(SystemConfig.agency_id.is_(None), SystemConfig.key == key)
        if (await session.execute(stmt)).scalar_one_or_none() is None:
            session.add(SystemConfig(agency_id=None, key=key, value=value))
    return len(_DEFAULT_CONFIG)


async def _ensure_rules(session: AsyncSession) -> int:
    """Insert each baseline rule as a GLOBAL rule (agency_id NULL) if absent; return the count.

    Sourced from the one canonical `DEFAULT_RULE_DEFINITIONS` so the seeded rows, the engine
    code defaults, and the rules reference never drift (no duplication, rule 5).
    """
    for definition in DEFAULT_RULE_DEFINITIONS:
        stmt = select(AmlRule).where(AmlRule.agency_id.is_(None), AmlRule.code == definition.code)
        if (await session.execute(stmt)).scalar_one_or_none() is None:
            session.add(
                AmlRule(
                    agency_id=None,
                    code=definition.code,
                    name=definition.name,
                    rule_type=definition.rule_type,
                    params=definition.params,
                    severity=Severity(definition.severity),
                    weight=definition.weight,
                    enabled=definition.enabled,
                    version=definition.version,
                )
            )
    return len(DEFAULT_RULE_DEFINITIONS)


async def _ensure_fixture_model(session: AsyncSession) -> None:
    """Insert the active fixture model (dataset → run → ACTIVE version → pointer) if absent."""
    metrics = _fixture_metrics()
    if await session.get(TrainingDataset, _FIXTURE_DATASET_ID) is None:
        session.add(
            TrainingDataset(
                id=_FIXTURE_DATASET_ID,
                snapshot_query={"source": "synthetic"},
                label_window="synthetic",
                row_count=0,
                feature_spec=_FIXTURE_FEATURE_SPEC,
                content_hash=_feature_spec_hash(_FIXTURE_FEATURE_SPEC),
            )
        )
    if await session.get(ModelTrainingRun, _FIXTURE_TRAINING_RUN_ID) is None:
        session.add(
            ModelTrainingRun(
                id=_FIXTURE_TRAINING_RUN_ID,
                trigger=ModelTrigger.MANUAL,
                dataset_id=_FIXTURE_DATASET_ID,
                status=JobStatus.SUCCEEDED,
                params={"fixture": True},
                metrics=metrics,
                artifact_uri=_FIXTURE_ARTIFACT_URI,
            )
        )
    if await session.get(ModelVersion, _FIXTURE_MODEL_VERSION_ID) is None:
        session.add(
            ModelVersion(
                id=_FIXTURE_MODEL_VERSION_ID,
                version_label=_FIXTURE_MODEL_LABEL,
                training_run_id=_FIXTURE_TRAINING_RUN_ID,
                artifact_uri=_FIXTURE_ARTIFACT_URI,
                feature_spec=_FIXTURE_FEATURE_SPEC,
                metrics=metrics,
                status=ModelVersionStatus.ACTIVE,
                notes="Active fixture model: real XGBoost+SHAP artifact (train-model --fixture).",
            )
        )
    if (await session.execute(select(ModelDeployment).limit(1))).scalar_one_or_none() is None:
        session.add(
            ModelDeployment(
                id=_FIXTURE_DEPLOYMENT_ID,
                active_version_id=_FIXTURE_MODEL_VERSION_ID,
                canary_percent=0,
            )
        )


async def _ensure_training_labels(session: AsyncSession) -> int:
    """Seed pre-matured reviewed labels (+ their completed runs) so retrain is eligible (§9.4).

    Idempotent: if the demo agency already has labels, leave them untouched. Otherwise label the
    first transactions with a balanced class cycle, each backed by a completed `analysis_runs` row
    (the label's FK) and matured in the past so the retrain Job counts them immediately.
    """
    existing = (
        await session.execute(
            select(func.count())
            .select_from(TrainingLabel)
            .where(TrainingLabel.agency_id == DEMO_AGENCY_ID)
        )
    ).scalar_one()
    if existing:
        return int(existing)
    transaction_ids = list(
        (
            await session.execute(
                select(Transaction.id)
                .where(Transaction.agency_id == DEMO_AGENCY_ID)
                .order_by(Transaction.ingested_at.asc(), Transaction.id.asc())
                .limit(_DEMO_LABEL_COUNT)
            )
        )
        .scalars()
        .all()
    )
    matured_at = datetime.now(UTC) - timedelta(days=_DEMO_LABEL_MATURED_DAYS_AGO)
    for index, transaction_id in enumerate(transaction_ids):
        run = AnalysisRun(
            agency_id=DEMO_AGENCY_ID,
            transaction_id=transaction_id,
            status=RunStatus.COMPLETED,
            model_version=_FIXTURE_MODEL_LABEL,
        )
        session.add(run)
        await session.flush()
        session.add(
            TrainingLabel(
                agency_id=DEMO_AGENCY_ID,
                transaction_id=transaction_id,
                run_id=run.id,
                label=_DEMO_LABEL_CYCLE[index % len(_DEMO_LABEL_CYCLE)],
                source=LabelSource.ANALYST_REVIEW,
                matured_at=matured_at,
                created_by=DEMO_USER_ID,
            )
        )
    return len(transaction_ids)


async def _ensure_alerts(session: AsyncSession) -> tuple[int, int]:
    """Seed a PHI-free open-alert queue + SAR drafts so local-demo shows a populated dashboard.

    Idempotent: if the demo agency already has alerts, leave everything untouched. Otherwise expand
    `_ALERT_PLAN` into alerts — each backed by a completed `analysis_runs` row over a seeded
    transaction, with force-review flags from the canonical `compute_review_flags` — attach a SAR
    draft per the plan (some filed/approved), and record one triage action per non-open alert.
    Returns `(alerts, sar_drafts)` ensured. All entities are scoped to the demo agency.
    """
    existing_alerts = (
        await session.execute(
            select(func.count()).select_from(Alert).where(Alert.agency_id == DEMO_AGENCY_ID)
        )
    ).scalar_one()
    if existing_alerts:
        existing_sars = (
            await session.execute(
                select(func.count())
                .select_from(SarDraft)
                .where(SarDraft.agency_id == DEMO_AGENCY_ID)
            )
        ).scalar_one()
        return int(existing_alerts), int(existing_sars)

    transaction_ids = list(
        (
            await session.execute(
                select(Transaction.id)
                .where(Transaction.agency_id == DEMO_AGENCY_ID)
                .order_by(Transaction.ingested_at.asc(), Transaction.id.asc())
            )
        )
        .scalars()
        .all()
    )
    if not transaction_ids:
        return 0, 0

    now = datetime.now(UTC)
    prompt_hash = hashlib.sha256(_DEMO_SAR_CONTENT.encode("utf-8")).hexdigest()
    alert_count = 0
    sar_count = 0
    index = 0
    for status, severity, count, sar_status in _ALERT_PLAN:
        risk_band, probability = _SEVERITY_SIGNAL[severity]
        for _ in range(count):
            transaction_id = transaction_ids[index % len(transaction_ids)]
            created_at = now - timedelta(
                minutes=_FIRST_ALERT_MINUTES_AGO + index * _ALERT_SPACING_MINUTES
            )
            index += 1
            run = AnalysisRun(
                agency_id=DEMO_AGENCY_ID,
                transaction_id=transaction_id,
                status=RunStatus.COMPLETED,
                risk_score=probability,
                risk_band=risk_band,
                model_version=_FIXTURE_MODEL_LABEL,
            )
            session.add(run)
            await session.flush()  # need run.id for the alert + SAR FKs
            flags = compute_review_flags(
                risk_band=risk_band,
                fraud_probability=probability,
                sar_status=sar_status.value if sar_status is not None else None,
                low_confidence_margin=_LOW_CONFIDENCE_MARGIN,
            )
            alert = Alert(
                agency_id=DEMO_AGENCY_ID,
                transaction_id=transaction_id,
                run_id=run.id,
                status=status,
                severity=severity,
                assigned_to=DEMO_USER_ID if status in _ASSIGNED_STATUSES else None,
                review_flags=flags,
                created_at=created_at,
            )
            session.add(alert)
            await session.flush()  # need alert.id for the SAR + action FKs
            alert_count += 1
            if sar_status is not None:
                session.add(
                    SarDraft(
                        agency_id=DEMO_AGENCY_ID,
                        run_id=run.id,
                        alert_id=alert.id,
                        version=1,
                        model_id=_FIXTURE_MODEL_LABEL,
                        prompt_version=_DEMO_SAR_PROMPT_VERSION,
                        prompt_hash=prompt_hash,
                        content=_DEMO_SAR_CONTENT,
                        structured={"summary": _DEMO_SAR_CONTENT},
                        citations=list(_DEMO_SAR_CITATIONS),
                        status=sar_status,
                        cost_usd=Decimal("0.0"),
                        created_by=DEMO_USER_ID,
                        reviewed_by=(
                            _DEMO_REVIEWER_ID if sar_status in _REVIEWED_SAR_STATUSES else None
                        ),
                        created_at=created_at,
                    )
                )
                sar_count += 1
            action = _ACTION_FOR_STATUS.get(status)
            if action is not None:
                session.add(
                    AlertAction(
                        agency_id=DEMO_AGENCY_ID,
                        alert_id=alert.id,
                        actor_id=DEMO_USER_ID,
                        action=action,
                        from_status=AlertStatus.OPEN.value,
                        to_status=status.value,
                        created_at=created_at,
                    )
                )
    return alert_count, sar_count


async def _record_seed_job(session: AsyncSession, summary: SeedSummary) -> None:
    """Upsert the single seed job_executions row (idempotent audit of the seed run)."""
    job = await session.get(JobExecution, _SEED_JOB_ID)
    result = summary.model_dump()
    if job is None:
        session.add(
            JobExecution(
                id=_SEED_JOB_ID,
                agency_id=DEMO_AGENCY_ID,
                job_type=JobType.SEED,
                status=JobStatus.SUCCEEDED,
                payload={"phase": 5},
                result=result,
                attempts=1,
            )
        )
    else:
        job.status = JobStatus.SUCCEEDED
        job.result = result
        job.attempts = job.attempts + 1


async def seed(session: AsyncSession, settings: AppSettings | None = None) -> SeedSummary:
    """Idempotently upsert the Phase 2 demo dataset into the session (caller commits)."""
    resolved_settings = settings or get_settings()
    await _ensure_agency(session)
    await session.flush()  # the agency must exist before its FKs (users, config, job)
    user_count = await _ensure_users(session)
    user_count += await _ensure_bootstrap_admin(session, resolved_settings)
    config_count = await _ensure_config(session)
    rules_count = await _ensure_rules(session)
    await _ensure_fixture_model(session)
    transaction_count = await seed_sample_transactions(session, DEMO_AGENCY_ID)
    await session.flush()  # transactions must exist before their labels' runs reference them
    label_count = await _ensure_training_labels(session)
    alert_count, sar_count = await _ensure_alerts(session)
    summary = SeedSummary(
        agencies=1,
        users=user_count,
        config_keys=config_count,
        rules=rules_count,
        model_versions=1,
        deployments=1,
        transactions=transaction_count,
        training_labels=label_count,
        alerts=alert_count,
        sar_drafts=sar_count,
    )
    await _record_seed_job(session, summary)
    await session.flush()
    return summary


async def _amain() -> int:
    """Build the engine from settings and run the seed in one transaction (dev/demo only)."""
    settings = get_settings()
    if settings.environment == "prod":
        print("seed refused: never seeds in prod (FraudLens governance §9.3)")
        return 1
    engine = create_engine_from_settings(settings)
    if engine is None:
        print("seed failed: DATABASE_URL is not configured")
        return 1
    sessionmaker = build_sessionmaker(engine)
    try:
        async with sessionmaker() as session:
            summary = await seed(session, settings)
            await session.commit()
    finally:
        await engine.dispose()
    print(
        "seed OK: "
        f"{summary.agencies} agency, {summary.users} users, {summary.config_keys} config keys, "
        f"{summary.rules} baseline rules, {summary.model_versions} active fixture model, "
        f"{summary.transactions} transactions, {summary.training_labels} matured labels, "
        f"{summary.alerts} alerts, {summary.sar_drafts} SAR drafts"
    )
    return 0


def main() -> int:
    """CLI entry point: run the async seed and return its exit code."""
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())
