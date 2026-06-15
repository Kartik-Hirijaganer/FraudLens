"""Phase 10 canary-routing wiring tests (plan §10.5, §16 Phase 10). Verify the registry resolves a
`CanaryDeployment` from the pointer, `resolve_scoring_pointer` routes per-transaction (active-only
when no/0% canary; to the canary at 100% with the active model as the last-known-good fallback), and
the `ScorerAdapter` flows the `was_canary` decision onto the `ScoreResult` so the scoring step's
inference log records which arm scored ("canary logs both models")."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import (
    JobStatus,
    ModelDeployment,
    ModelTrainingRun,
    ModelTrigger,
    ModelVersion,
    ModelVersionStatus,
    TrainingDataset,
)
from fraudlens_backend.db.repositories import ModelLifecycleRepository, ModelRegistryRepository
from fraudlens_backend.pipeline_wiring import ScorerAdapter, resolve_scoring_pointer
from fraudlens_core import RuleContext
from fraudlens_ml.scoring import DeploymentPointer, ModelCache, Scorer
from seed import seed

_DEMO_USER_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")


async def _set_canary(session: AsyncSession, *, label: str, percent: int) -> ModelVersion:
    """Create a canary candidate version and point the seeded deployment at it."""
    dataset = TrainingDataset(
        snapshot_query={}, label_window="t", row_count=0, feature_spec={}, content_hash="c" * 64
    )
    session.add(dataset)
    await session.flush()
    run = ModelTrainingRun(
        trigger=ModelTrigger.MANUAL, dataset_id=dataset.id, status=JobStatus.SUCCEEDED
    )
    session.add(run)
    await session.flush()
    version = ModelVersion(
        version_label=label,
        training_run_id=run.id,
        artifact_uri=label,
        feature_spec={"features": ["amount_log"]},
        metrics={"pr_auc": 0.61},
        status=ModelVersionStatus.CANARY,
    )
    session.add(version)
    await session.flush()
    deployment = (await session.execute(select(ModelDeployment))).scalar_one()
    deployment.canary_version_id = version.id
    deployment.canary_percent = percent
    await session.flush()
    return version


async def test_build_canary_deployment_active_only(db_session: AsyncSession) -> None:
    await seed(db_session)
    canary = await ModelRegistryRepository(db_session).build_canary_deployment()
    assert canary is not None
    assert canary.active_version_label == "v0-fixture"
    assert canary.canary_version_label is None
    assert canary.canary_percent == 0


async def test_build_canary_deployment_with_canary(db_session: AsyncSession) -> None:
    await seed(db_session)
    await _set_canary(db_session, label="canary-v1", percent=50)
    canary = await ModelRegistryRepository(db_session).build_canary_deployment()
    assert canary is not None
    assert canary.canary_version_label == "canary-v1"
    assert canary.canary_percent == 50


async def test_resolve_pointer_active_only(db_session: AsyncSession) -> None:
    await seed(db_session)
    registry = ModelRegistryRepository(db_session)
    pointer, was_canary = await resolve_scoring_pointer(registry, routing_key="txn-1")
    assert was_canary is False
    assert pointer is not None
    assert pointer.active_version_label == "v0-fixture"


async def test_resolve_routes_to_canary_at_full_percent(db_session: AsyncSession) -> None:
    await seed(db_session)
    await _set_canary(db_session, label="canary-v1", percent=100)
    registry = ModelRegistryRepository(db_session)
    pointer, was_canary = await resolve_scoring_pointer(registry, routing_key="txn-1")
    assert was_canary is True
    assert pointer is not None
    assert pointer.active_version_label == "canary-v1"
    # The active model is retained as the canary's last-known-good fallback.
    assert pointer.previous_version_label == "v0-fixture"


async def test_resolve_stays_active_at_zero_percent(db_session: AsyncSession) -> None:
    await seed(db_session)
    await _set_canary(db_session, label="canary-v1", percent=0)
    registry = ModelRegistryRepository(db_session)
    pointer, was_canary = await resolve_scoring_pointer(registry, routing_key="txn-1")
    assert was_canary is False
    assert pointer is not None
    assert pointer.active_version_label == "v0-fixture"


async def _activate_new_version(session: AsyncSession, *, label: str) -> None:
    """Create a candidate and promote it shadow→approve→active (the in-place pointer flip)."""
    dataset = TrainingDataset(
        snapshot_query={}, label_window="t", row_count=0, feature_spec={}, content_hash="d" * 64
    )
    session.add(dataset)
    await session.flush()
    run = ModelTrainingRun(
        trigger=ModelTrigger.MANUAL, dataset_id=dataset.id, status=JobStatus.SUCCEEDED
    )
    session.add(run)
    await session.flush()
    version = ModelVersion(
        version_label=label,
        training_run_id=run.id,
        artifact_uri=label,
        feature_spec={"features": ["amount_log"]},
        metrics={"pr_auc": 0.61},
        status=ModelVersionStatus.CANDIDATE,
    )
    session.add(version)
    await session.flush()
    lifecycle = ModelLifecycleRepository(session)
    await lifecycle.promote_to_shadow(version)
    await lifecycle.approve(version, approved_by=_DEMO_USER_ID)
    await lifecycle.activate(version, updated_by=_DEMO_USER_ID)


async def test_pointer_reloads_after_activation(db_session: AsyncSession) -> None:
    # A subsequent run re-resolves the deployment pointer, so a flipped active is served next run
    # with NO redeploy (plan §5.4 / §10.5) — the dedicated "pointer reload" proof.
    await seed(db_session)
    registry = ModelRegistryRepository(db_session)
    first, _ = await resolve_scoring_pointer(registry, routing_key="txn-reload")
    assert first is not None
    assert first.active_version_label == "v0-fixture"

    await _activate_new_version(db_session, label="reload-cand")

    second, _ = await resolve_scoring_pointer(registry, routing_key="txn-reload")
    assert second is not None
    assert second.active_version_label == "reload-cand"  # the next run sees the flipped pointer
    assert second.previous_version_label == "v0-fixture"  # the prior active is last-known-good


async def test_resolve_model_override_beats_canary(db_session: AsyncSession) -> None:
    await seed(db_session)
    await _set_canary(db_session, label="canary-v1", percent=100)  # would otherwise win every key
    registry = ModelRegistryRepository(db_session)
    pointer, was_canary = await resolve_scoring_pointer(
        registry, routing_key="txn-1", model_override="v0-fixture"
    )
    assert was_canary is False  # an explicit override is not a canary-routing decision
    assert pointer is not None
    assert pointer.active_version_label == "v0-fixture"  # the override beats the 100% canary


async def test_resolve_model_override_unknown_falls_through(db_session: AsyncSession) -> None:
    await seed(db_session)
    registry = ModelRegistryRepository(db_session)
    pointer, was_canary = await resolve_scoring_pointer(
        registry, routing_key="txn-1", model_override="does-not-exist"
    )
    assert was_canary is False
    assert pointer is not None
    assert pointer.active_version_label == "v0-fixture"  # defensive fallthrough to active


def test_scorer_adapter_marks_canary(
    fixture_model_dir: Path, make_rule_context: Callable[..., RuleContext]
) -> None:
    cache = ModelCache(fixture_model_dir.parent)
    pointer = DeploymentPointer(
        active_version_label=fixture_model_dir.name, active_artifact_uri=fixture_model_dir.name
    )
    result = ScorerAdapter(Scorer(cache), pointer, was_canary=True).score(make_rule_context())
    assert result.was_canary is True
    assert 0.0 <= result.fraud_probability <= 1.0
    assert result.model_version_label == fixture_model_dir.name
