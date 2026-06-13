"""Summary: Idempotent dev/demo seed (plan §9.3). Re-runnable safely — every entity is
upserted by its natural key (or a fixed id), so `make db-seed` (and `make local-demo`) can
run repeatedly without creating duplicates. It seeds the foundation the schema can hold: the
demo agency + its analyst/reviewer/admin users (shared with the auth dev-bypass via
fraudlens_backend.demo), the default global `system_config` tunables, the **active fixture
model** (a training dataset → training run → ACTIVE model version → the single deployment
pointer), and — added in Phase 3 — the curated synthetic IEEE-CIS transactions (masked at
ingest via the shared importer) so `make local-demo` shows real, listable transactions. The
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
)
from fraudlens_backend.db.session import build_sessionmaker, create_engine_from_settings
from fraudlens_backend.demo import (
    DEMO_AGENCY_ID,
    DEMO_AGENCY_NAME,
    DEMO_AGENCY_SLUG,
    DEMO_USERS,
)
from fraudlens_backend.settings import get_settings
from fraudlens_core import DEFAULT_RULE_DEFINITIONS
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
                    agency_id=DEMO_AGENCY_ID,
                    email=spec.email,
                    display_name=spec.display_name,
                    role=spec.role,
                )
            )
    return len(DEMO_USERS)


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


async def seed(session: AsyncSession) -> SeedSummary:
    """Idempotently upsert the Phase 2 demo dataset into the session (caller commits)."""
    await _ensure_agency(session)
    await session.flush()  # the agency must exist before its FKs (users, config, job)
    user_count = await _ensure_users(session)
    config_count = await _ensure_config(session)
    rules_count = await _ensure_rules(session)
    await _ensure_fixture_model(session)
    transaction_count = await seed_sample_transactions(session, DEMO_AGENCY_ID)
    summary = SeedSummary(
        agencies=1,
        users=user_count,
        config_keys=config_count,
        rules=rules_count,
        model_versions=1,
        deployments=1,
        transactions=transaction_count,
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
            summary = await seed(session)
            await session.commit()
    finally:
        await engine.dispose()
    print(
        "seed OK: "
        f"{summary.agencies} agency, {summary.users} users, {summary.config_keys} config keys, "
        f"{summary.rules} baseline rules, {summary.model_versions} active fixture model, "
        f"{summary.transactions} transactions"
    )
    return 0


def main() -> int:
    """CLI entry point: run the async seed and return its exit code."""
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())
