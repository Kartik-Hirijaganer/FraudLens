"""Summary: Idempotent dev/demo foundation seed (plan §9.3). Re-runnable safely — every
entity is upserted by its natural key (or a fixed id), so `make db-seed` and local-demo bootstrap
can run repeatedly without duplicates. It creates only the shared application foundation: the
CONFIGURED demo agency and its personas (`config/portfolio-demo.yaml`, so this script knows no
demo identity values), default global `system_config` tunables, six global baseline AML rules,
and the active committed fixture-model pointer. Transactions come from the separate IBM AML demo
ingest and alerts/SARs come only from the investigation pipeline; this seed never fabricates
operational evidence. The seed execution itself is recorded in `job_executions`. Refuses to run
when `environment == "prod"`.

Key classes:
- SeedSummary: counts of the demo entities the seed ensured exist.

Key functions:
- seed: idempotently upsert the demo dataset into a session (no commit).
- main: build the engine from settings, run the seed in a transaction (dev/demo only).

Notes:
- Fixed UUIDs make the seed deterministic and idempotent across runs. The agency/persona ids
  come from `config/portfolio-demo.yaml`; only the fixture dataset/run/version/deployment and
  the seed job itself keep script-owned ids (they are infrastructure, not demo identity).
- The fixture model carries only feature NAMES + a content hash — no PHI, no agency_id as a
  feature (ADR-015); its ACTIVE pointer resolves to the committed real XGBoost+SHAP artifact
  bundle (`make train-model --fixture`), so local-demo scores via a real model (Phase 5).
- No transactions, analysis runs, labels, alerts, actions, or SARs are created here. Behavioral
  tests build their own scoped fixtures; local demo data enters through the masked IBM importer.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import (
    Agency,
    AmlRule,
    JobExecution,
    JobStatus,
    JobType,
    ModelDeployment,
    ModelTrainingRun,
    ModelTrigger,
    ModelVersion,
    ModelVersionStatus,
    Severity,
    SystemConfig,
    TrainingDataset,
    User,
    UserRole,
)
from fraudlens_backend.db.repositories.model_registry import FIXTURE_MODEL_LABEL
from fraudlens_backend.db.session import build_sessionmaker, create_engine_from_settings
from fraudlens_backend.portfolio_demo import PortfolioDemoConfig, load_portfolio_demo_config
from fraudlens_backend.settings import AppSettings, get_settings
from fraudlens_core import DEFAULT_RULE_DEFINITIONS, RiskPolicy
from fraudlens_ml.scoring import ModelGates, current_feature_spec

REPO_ROOT = Path(__file__).resolve().parents[1]

# Fixed ids so the seed is deterministic + idempotent (re-runs update, never duplicate).
_SEED_JOB_ID = uuid.UUID("22222222-2222-4222-8222-000000000001")
_FIXTURE_DATASET_ID = uuid.UUID("22222222-2222-4222-8222-000000000002")
_FIXTURE_TRAINING_RUN_ID = uuid.UUID("22222222-2222-4222-8222-000000000003")
_FIXTURE_MODEL_VERSION_ID = uuid.UUID("22222222-2222-4222-8222-000000000004")
_FIXTURE_DEPLOYMENT_ID = uuid.UUID("22222222-2222-4222-8222-000000000005")

# The fixture label lives in the registry repository so the seed (which installs it) and the
# portfolio-demo bootstrap (which may displace it) share one value, never two copies (rule 5).
_FIXTURE_MODEL_LABEL = FIXTURE_MODEL_LABEL

# The fixture's artifact uri (relative to settings.model_artifacts_dir = data/models) — the
# committed Phase 5 bundle the scorer's active pointer resolves to (`make train-model --fixture`).
_FIXTURE_ARTIFACT_URI = _FIXTURE_MODEL_LABEL

# Tenant-safe fixture feature spec: feature NAMES only — never PHI or agency_id (ADR-015).
# Sourced from the one canonical fraudlens-ml feature spec so the seeded fixture row matches
# the committed artifact the scorer loads (no duplication, rule 5).
_FIXTURE_FEATURE_SPEC: dict[str, Any] = current_feature_spec().model_dump()

# Default GLOBAL (agency_id NULL) runtime tunables (plan §9.1 `system_config` keys). The
# model-promotion gates are the canonical §10.5.1 ModelGates defaults (no duplicated thresholds).
_DEFAULT_CONFIG: dict[str, Any] = {
    "riskBandThresholds": {"low": 0.0, "medium": 0.3, "high": 0.6, "critical": 0.85},
    "alertThreshold": 0.6,
    # Sourced from the core policy so the seeded key IS the documented fallback, never a
    # second copy of it (`load_risk_policy` reads this key; `risk.py` keeps the fallback).
    "riskBlendModelWeight": RiskPolicy().model_weight,
    "llmDailyBudgetUsd": 5.0,
    "llmSessionBudgetUsd": 0.5,
    "defaultModelId": _FIXTURE_MODEL_LABEL,
    "retentionDays": 365,
    "labelMaturityDays": 7,
    "canaryPercent": 0,
    "modelGates": ModelGates().model_dump(by_alias=True),
    "featureFlags": {"phiNerMasking": False},
}


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


def _feature_spec_hash(spec: dict[str, Any]) -> str:
    """Return a deterministic content hash of the fixture feature spec (reproducibility)."""
    canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _ensure_agency(session: AsyncSession, config: PortfolioDemoConfig) -> None:
    """Insert the configured demo agency at its fixed id if absent."""
    agency = config.agency
    if await session.get(Agency, agency.id) is None:
        session.add(Agency(id=agency.id, name=agency.name, slug=agency.slug))


async def _ensure_users(session: AsyncSession, config: PortfolioDemoConfig) -> int:
    """Ensure each persona's FIXED seed actor row exists at `seed_user_id`; return the count.

    Keyed on the id, not the email, because the two are not interchangeable: the bootstrap records
    `alert_actions.actor_id` / `sar_drafts.reviewed_by` against `seed_user_id`, so that row must
    exist even when the login address is already taken. It is taken whenever
    `provision_demo_auth.py` ran first — it mirrors a Supabase auth UUID onto the login email, and
    an email-keyed check would then skip, leaving the transitions to die on a foreign key. In that
    order the seed actor takes the same derived history address provisioning would have moved it
    to, so both rows coexist under the global `uq_users_email` constraint and the seed converges on
    the intended state whichever script ran first.
    """
    agency_id = config.agency.id
    for persona in config.personas:
        if await session.get(User, persona.seed_user_id) is not None:
            continue
        stmt = select(User).where(User.agency_id == agency_id, User.email == persona.email)
        displaced = (await session.execute(stmt)).scalar_one_or_none() is not None
        session.add(
            User(
                id=persona.seed_user_id,
                agency_id=agency_id,
                email=config.history_email(persona) if displaced else persona.email,
                display_name=persona.display_name,
                role=persona.role,
            )
        )
    return len(config.personas)


async def _ensure_bootstrap_admin(
    session: AsyncSession, settings: AppSettings, config: PortfolioDemoConfig
) -> int:
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
                agency_id=config.agency.id,
                email=settings.bootstrap_admin_email,
                display_name=settings.bootstrap_admin_display_name,
                role=UserRole.ADMIN,
            )
        )
    else:
        user.agency_id = config.agency.id
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


async def _record_seed_job(
    session: AsyncSession, summary: SeedSummary, config: PortfolioDemoConfig
) -> None:
    """Upsert the single seed job_executions row (idempotent audit of the seed run)."""
    job = await session.get(JobExecution, _SEED_JOB_ID)
    result = summary.model_dump()
    if job is None:
        session.add(
            JobExecution(
                id=_SEED_JOB_ID,
                agency_id=config.agency.id,
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


async def seed(
    session: AsyncSession,
    settings: AppSettings | None = None,
    config: PortfolioDemoConfig | None = None,
) -> SeedSummary:
    """Idempotently upsert the configured demo foundation into the session (caller commits)."""
    resolved_settings = settings or get_settings()
    resolved_config = config or load_portfolio_demo_config(settings=resolved_settings)
    await _ensure_agency(session, resolved_config)
    await session.flush()  # the agency must exist before its FKs (users, config, job)
    user_count = await _ensure_users(session, resolved_config)
    user_count += await _ensure_bootstrap_admin(session, resolved_settings, resolved_config)
    config_count = await _ensure_config(session)
    rules_count = await _ensure_rules(session)
    await _ensure_fixture_model(session)
    summary = SeedSummary(
        agencies=1,
        users=user_count,
        config_keys=config_count,
        rules=rules_count,
        model_versions=1,
        deployments=1,
    )
    await _record_seed_job(session, summary, resolved_config)
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
        f"{summary.rules} baseline rules, {summary.model_versions} active fixture model"
    )
    return 0


def main() -> int:
    """CLI entry point: run the async seed and return its exit code."""
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())
