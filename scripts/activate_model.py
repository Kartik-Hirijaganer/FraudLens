"""Summary: Register + promote the best locally trained, gates-PASSED model bundle to ACTIVE
(full-IBM plan Phase 6). `make run` rebuilds the database from scratch, which discards the
registry rows an earlier `make train-aml` created — but the artifact bundle (+ its
`manifest.json` provenance sidecar) survives under the gitignored `model_artifacts_dir`. This
command bridges the two: it scans the artifacts root, checksum-verifies each non-fixture bundle
by actually loading it, requires `gates_passed == 1` (a failing candidate is NEVER promoted —
honesty over a populated demo), idempotently re-registers the dataset/run/version/evaluation
rows from the bundle + sidecar, and then walks the REAL human-gated lifecycle chain
(candidate → shadow → approve-as-demo-admin → activate) through `ModelLifecycleRepository`, so
the pointer flip, archival of the outgoing fixture, and rollback previous-active all behave
exactly as an operator-driven promotion. When no eligible bundle exists it leaves the seeded
fixture active and prints the training command — CI and fresh clones stay hermetic.

Key classes:
- EligibleBundle: one verified, gates-passed artifact bundle + its manifest sidecar.

Key functions:
- discover_bundles: scan the artifacts root for loadable, gates-passed non-fixture bundles.
- register_bundle: idempotently recreate the registry rows for a bundle in the target database.
- promote_to_active: drive the candidate→shadow→approve→activate lifecycle transitions.
- main: CLI — pick the best (or --label) bundle, register, promote, update defaultModelId.

Notes:
- Loading (not just parsing) each bundle re-verifies the booster checksum, so a corrupt
  artifact is skipped instead of being promoted and failing at first score.
- Approval is stamped with the seeded demo ADMIN user (or any admin present), keeping the
  approved_by audit trail meaningful in the demo database; no admin -> refuse, never NULL-forge.
- The global `defaultModelId` system_config row is updated to the promoted label so the seeded
  config stays consistent with the live pointer.
- Refuses `environment == "prod"` (FraudLens governance: demo promotion is a local-dev bridge;
  prod promotions go through the admin lifecycle API).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import (
    JobExecution,
    JobStatus,
    JobType,
    ModelDeployment,
    ModelEvaluation,
    ModelTrainingRun,
    ModelTrigger,
    ModelVersion,
    ModelVersionStatus,
    SystemConfig,
    TrainingDataset,
    User,
    UserRole,
)
from fraudlens_backend.db.repositories.model_lifecycle import (
    ModelLifecycleRepository,
    can_approve,
    can_shadow,
)
from fraudlens_backend.db.session import build_sessionmaker, create_engine_from_settings
from fraudlens_backend.demo import DEMO_USERS
from fraudlens_backend.settings import get_settings
from fraudlens_ml.scoring import ArtifactError, load_artifact
from fraudlens_ml.scoring.artifacts import ModelArtifactMetadata
from train_model import _FIXTURE_LABEL, _MANIFEST_SIDECAR, _artifacts_root

_GATES_PASSED_KEY = "gates_passed"
_PR_AUC_KEY = "pr_auc"
_DEFAULT_MODEL_CONFIG_KEY = "defaultModelId"


@dataclass(frozen=True)
class EligibleBundle:
    """One verified, gates-passed artifact bundle plus its dataset-manifest sidecar."""

    directory: Path
    metadata: ModelArtifactMetadata
    manifest: dict[str, Any]
    seed: int
    rows: int


def _read_sidecar(directory: Path) -> tuple[dict[str, Any], int, int] | None:
    """Read a bundle's manifest sidecar; None when absent/invalid (bundle is not registrable)."""
    sidecar = directory / _MANIFEST_SIDECAR
    if not sidecar.is_file():
        return None
    try:
        payload = json.loads(sidecar.read_text("utf-8"))
        return dict(payload["manifest"]), int(payload["seed"]), int(payload["rows"])
    except (ValueError, KeyError, TypeError):
        return None


def discover_bundles(root: Path, *, label: str | None) -> list[EligibleBundle]:
    """Return loadable, gates-passed, non-fixture bundles under the artifacts root.

    Every candidate is fully LOADED (booster checksum re-verified); corrupt bundles, the
    committed fixture, gates-failed candidates, and sidecar-less bundles are skipped with a
    printed reason so the outcome is auditable.
    """
    eligible: list[EligibleBundle] = []
    if not root.is_dir():
        return eligible
    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        if directory.name == _FIXTURE_LABEL:
            continue
        if label is not None and directory.name != label:
            continue
        try:
            loaded = load_artifact(directory)
        except ArtifactError as exc:
            print(f">> activate-model: skipping {directory.name}: {exc}")
            continue
        metadata = ModelArtifactMetadata.model_validate_json(
            (directory / "metadata.json").read_text("utf-8")
        )
        if float(loaded.metrics.get(_GATES_PASSED_KEY, 0.0)) != 1.0:
            print(f">> activate-model: skipping {directory.name}: gates not passed")
            continue
        sidecar = _read_sidecar(directory)
        if sidecar is None:
            print(f">> activate-model: skipping {directory.name}: missing manifest sidecar")
            continue
        manifest, seed, rows = sidecar
        eligible.append(
            EligibleBundle(
                directory=directory, metadata=metadata, manifest=manifest, seed=seed, rows=rows
            )
        )
    return eligible


async def _resolve_admin_id(session: AsyncSession) -> Any:
    """Return the approving admin: the seeded demo admin, else any admin, else None."""
    demo_admin = next(spec for spec in DEMO_USERS if spec.role == UserRole.ADMIN)
    admin = await session.get(User, demo_admin.user_id)
    if admin is not None:
        return admin.id
    stmt = select(User).where(User.role == UserRole.ADMIN).order_by(User.created_at).limit(1)
    fallback = (await session.execute(stmt)).scalar_one_or_none()
    return None if fallback is None else fallback.id


async def register_bundle(session: AsyncSession, bundle: EligibleBundle) -> ModelVersion:
    """Idempotently recreate the dataset/run/version/evaluation rows for a bundle."""
    label = bundle.metadata.version_label
    existing = (
        await session.execute(select(ModelVersion).where(ModelVersion.version_label == label))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    manifest = bundle.manifest
    dataset = TrainingDataset(
        snapshot_query=manifest.get("snapshot_query", {}),
        label_window=str(manifest.get("label_window", manifest.get("source", "unknown"))),
        row_count=int(manifest.get("row_count", 0)),
        feature_spec=bundle.metadata.feature_spec.model_dump(),
        content_hash=str(manifest.get("content_hash", "")),
    )
    session.add(dataset)
    await session.flush()
    training_run = ModelTrainingRun(
        trigger=ModelTrigger.MANUAL,
        dataset_id=dataset.id,
        status=JobStatus.SUCCEEDED,
        params={"registeredFromArtifact": True, "seed": bundle.seed, "rows": bundle.rows},
        metrics=dict(bundle.metadata.metrics),
        artifact_uri=label,
    )
    session.add(training_run)
    await session.flush()
    version = ModelVersion(
        version_label=label,
        training_run_id=training_run.id,
        artifact_uri=label,
        feature_spec=bundle.metadata.feature_spec.model_dump(),
        metrics=dict(bundle.metadata.metrics),
        status=ModelVersionStatus.CANDIDATE,
        notes="Registered from local artifact bundle by activate_model (full-IBM plan Phase 6).",
    )
    session.add(version)
    await session.flush()
    session.add(
        ModelEvaluation(
            model_version_id=version.id,
            baseline_version_id=None,
            metrics=dict(bundle.metadata.metrics),
            passed=True,
        )
    )
    session.add(
        JobExecution(
            agency_id=None,
            job_type=JobType.TRAIN,
            status=JobStatus.SUCCEEDED,
            payload={"action": "register_from_artifact", "version_label": label},
            result={"gates_passed": True, _PR_AUC_KEY: bundle.metadata.metrics.get(_PR_AUC_KEY)},
            attempts=1,
        )
    )
    await session.flush()
    return version


async def _ensure_passing_evaluation(
    session: AsyncSession,
    lifecycle: ModelLifecycleRepository,
    version: ModelVersion,
    bundle: EligibleBundle,
) -> None:
    """Guarantee the passing `model_evaluations` row the shadow transition requires."""
    if await lifecycle.has_passing_evaluation(version.id):
        return
    session.add(
        ModelEvaluation(
            model_version_id=version.id,
            baseline_version_id=None,
            metrics=dict(bundle.metadata.metrics),
            passed=True,
        )
    )
    await session.flush()


async def promote_to_active(
    session: AsyncSession, version: ModelVersion, bundle: EligibleBundle
) -> str:
    """Drive the real lifecycle transitions to ACTIVE; return a PHI-free outcome summary."""
    lifecycle = ModelLifecycleRepository(session)
    deployment = await lifecycle.get_deployment()
    if deployment is not None and deployment.active_version_id == version.id:
        return f"'{version.version_label}' is already the active model"
    admin_id = await _resolve_admin_id(session)
    if admin_id is None:
        raise RuntimeError("no admin user exists to approve the promotion — run the seed first")
    await _ensure_passing_evaluation(session, lifecycle, version, bundle)
    if version.status == ModelVersionStatus.CANDIDATE:
        if not can_shadow(version.status, has_passing_evaluation=True):
            raise RuntimeError(f"'{version.version_label}' cannot enter shadow")
        await lifecycle.promote_to_shadow(version)
    if version.status == ModelVersionStatus.SHADOW and version.approved_by is None:
        if not can_approve(version.status):
            raise RuntimeError(f"'{version.version_label}' cannot be approved")
        await lifecycle.approve(version, approved_by=admin_id)
    if deployment is None:
        # Bootstrap edge: no pointer row exists yet (seed not run with a fixture). Create the
        # pointer directly at this version so /readyz has a loadable active model.
        version.status = ModelVersionStatus.ACTIVE
        session.add(ModelDeployment(active_version_id=version.id, canary_percent=0))
        await session.flush()
    else:
        await lifecycle.activate(version, updated_by=admin_id)
    return f"activated '{version.version_label}'"


async def _update_default_model_config(session: AsyncSession, label: str) -> None:
    """Point the global `defaultModelId` config at the promoted label (upsert, global row)."""
    stmt = select(SystemConfig).where(
        SystemConfig.agency_id.is_(None), SystemConfig.key == _DEFAULT_MODEL_CONFIG_KEY
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        session.add(SystemConfig(agency_id=None, key=_DEFAULT_MODEL_CONFIG_KEY, value=label))
    else:
        # The seeded value is a bare JSON string (see scripts/seed.py); the ORM annotation is
        # dict-typed for the common case, so mirror the seed's shape under a cast.
        row.value = cast("dict[str, Any]", label)
    await session.flush()


async def _amain(label: str | None) -> int:
    """Discover, register, and promote the best gates-passed bundle (dev/demo only)."""
    settings = get_settings()
    if settings.environment == "prod":
        print("activate-model refused: prod promotions go through the admin lifecycle API")
        return 1
    root = _artifacts_root(settings)
    bundles = discover_bundles(root, label=label)
    if not bundles:
        if label is not None:
            print(f"activate-model failed: no eligible bundle '{label}' under {root}")
            return 1
        print(
            ">> activate-model: no gates-passed trained bundle found — the seeded fixture "
            "stays active. Train one with `make train-aml` (or --artifact-only)."
        )
        return 0
    best = max(bundles, key=lambda b: float(b.metadata.metrics.get(_PR_AUC_KEY, 0.0)))
    engine = create_engine_from_settings(settings)
    if engine is None:
        print("activate-model failed: DATABASE_URL is not configured")
        return 1
    try:
        async with build_sessionmaker(engine)() as session:
            version = await register_bundle(session, best)
            outcome = await promote_to_active(session, version, best)
            await _update_default_model_config(session, version.version_label)
            await session.commit()
    except RuntimeError as exc:
        print(f"activate-model failed: {exc}")
        return 1
    finally:
        await engine.dispose()
    metrics = best.metadata.metrics
    print(
        f"activate-model OK: {outcome} "
        f"(pr_auc={metrics.get(_PR_AUC_KEY, 0.0):.4f}, "
        f"recall@budget={metrics.get('recall_at_budget', 0.0):.3f}, "
        f"operating_points={'yes' if best.metadata.risk_thresholds else 'no'})"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: promote the best locally trained gates-passed bundle to ACTIVE."""
    parser = argparse.ArgumentParser(
        description="Register + promote the best gates-passed local model bundle."
    )
    parser.add_argument(
        "--label", default=None, help="Promote exactly this bundle label (default: best PR-AUC)."
    )
    args = parser.parse_args(argv)
    return asyncio.run(_amain(args.label))


if __name__ == "__main__":
    raise SystemExit(main())
