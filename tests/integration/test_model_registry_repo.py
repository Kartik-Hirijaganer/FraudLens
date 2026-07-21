"""ModelRegistryRepository tests (plan §16 Phase 5: registry resolution). Verify the
read-only registry repo resolves the active deployment into the scorer's DeploymentPointer —
including the previous-active last-known-good fallback (plan §10.6) — and fails to None when
there is no deployment or the active version is missing (so callers can fail closed)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import ModelDeployment, ModelVersion, ModelVersionStatus
from fraudlens_backend.db.repositories import ModelRegistryRepository
from seed import (
    _FIXTURE_MODEL_VERSION_ID,
    _FIXTURE_TRAINING_RUN_ID,
    seed,
)


async def test_build_pointer_none_without_deployment(db_session: AsyncSession) -> None:
    assert await ModelRegistryRepository(db_session).build_pointer() is None


async def test_build_pointer_resolves_seeded_active(db_session: AsyncSession) -> None:
    await seed(db_session)
    repo = ModelRegistryRepository(db_session)
    pointer = await repo.build_pointer()
    assert pointer is not None
    assert pointer.active_version_label == "v0-fixture"
    assert pointer.active_artifact_uri == "v0-fixture"
    assert pointer.previous_version_label is None  # no rollback history yet


async def test_build_pointer_includes_last_known_good(db_session: AsyncSession) -> None:
    await seed(db_session)
    # register a second version and point the deployment's previous-active at it
    previous = ModelVersion(
        version_label="v0-previous",
        training_run_id=_FIXTURE_TRAINING_RUN_ID,
        artifact_uri="v0-previous",
        feature_spec={"version": 1, "features": []},
        metrics={},
        status=ModelVersionStatus.ARCHIVED,
    )
    db_session.add(previous)
    await db_session.flush()
    deployment = await ModelRegistryRepository(db_session).get_active_deployment()
    assert deployment is not None
    deployment.previous_active_version_id = previous.id
    await db_session.flush()

    pointer = await ModelRegistryRepository(db_session).build_pointer()
    assert pointer is not None
    assert pointer.previous_version_label == "v0-previous"
    assert pointer.previous_artifact_uri == "v0-previous"


async def test_build_pointer_none_when_active_version_missing(db_session: AsyncSession) -> None:
    # a deployment pointing at a non-existent version resolves to None (caller fails closed)
    db_session.add(ModelDeployment(active_version_id=uuid.uuid4(), canary_percent=0))
    await db_session.flush()
    assert await ModelRegistryRepository(db_session).build_pointer() is None


async def test_build_latest_candidate_pointer_selects_newest_candidate(
    db_session: AsyncSession,
) -> None:
    await seed(db_session)
    now = datetime.now(UTC)
    db_session.add_all(
        [
            ModelVersion(
                version_label="candidate-older",
                training_run_id=_FIXTURE_TRAINING_RUN_ID,
                artifact_uri="candidate-older",
                feature_spec={},
                metrics={},
                status=ModelVersionStatus.CANDIDATE,
                created_at=now - timedelta(minutes=1),
            ),
            ModelVersion(
                version_label="candidate-newest",
                training_run_id=_FIXTURE_TRAINING_RUN_ID,
                artifact_uri="candidate-newest",
                feature_spec={},
                metrics={},
                status=ModelVersionStatus.CANDIDATE,
                created_at=now,
            ),
            ModelVersion(
                version_label="rejected-newer",
                training_run_id=_FIXTURE_TRAINING_RUN_ID,
                artifact_uri="rejected-newer",
                feature_spec={},
                metrics={},
                status=ModelVersionStatus.REJECTED,
                created_at=now + timedelta(minutes=1),
            ),
        ]
    )
    await db_session.flush()

    pointer = await ModelRegistryRepository(db_session).build_latest_candidate_pointer()

    assert pointer is not None
    assert pointer.active_version_label == "candidate-newest"
    assert pointer.active_artifact_uri == "candidate-newest"


async def test_build_latest_candidate_pointer_none_without_candidate(
    db_session: AsyncSession,
) -> None:
    await seed(db_session)
    assert await ModelRegistryRepository(db_session).build_latest_candidate_pointer() is None


async def test_list_versions_and_get_version(db_session: AsyncSession) -> None:
    await seed(db_session)
    repo = ModelRegistryRepository(db_session)
    versions = await repo.list_versions()
    assert [v.version_label for v in versions] == ["v0-fixture"]
    fetched = await repo.get_version(_FIXTURE_MODEL_VERSION_ID)
    assert fetched is not None
    assert fetched.version_label == "v0-fixture"
    assert await repo.get_version(uuid.uuid4()) is None
