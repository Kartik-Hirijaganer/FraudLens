"""Activation-script tests (full-IBM plan Phase 6): bundle discovery skips the fixture,
gates-failed, corrupt, and sidecar-less bundles; a discovered bundle is idempotently
registered and promoted to ACTIVE through the real lifecycle transitions (shadow -> approve
-> activate) with the outgoing fixture archived as the rollback previous-active; and the
global `defaultModelId` config follows the promotion. Fixtures are synthetic only."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from activate_model import (
    _update_default_model_config,
    discover_bundles,
    promote_to_active,
    register_bundle,
)
from fraudlens_backend.db.models import (
    ModelDeployment,
    ModelVersion,
    ModelVersionStatus,
    SystemConfig,
)
from fraudlens_core import ModelRiskThresholds
from fraudlens_ml.scoring import load_artifact, save_artifact
from seed import seed

_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "data" / "models" / "v0-fixture"


def _write_bundle(
    root: Path,
    label: str,
    *,
    gates_passed: float,
    pr_auc: float,
    with_sidecar: bool = True,
    with_thresholds: bool = True,
) -> Path:
    """Materialize a synthetic bundle (re-labeled fixture booster) under the artifacts root."""
    loaded = load_artifact(_FIXTURE_DIR)
    directory = root / label
    save_artifact(
        directory,
        loaded.booster,
        version_label=label,
        feature_spec=loaded.feature_spec,
        calibration=loaded.calibration,
        background=loaded.background,
        metrics={"pr_auc": pr_auc, "recall_at_budget": 0.66, "gates_passed": gates_passed},
        risk_thresholds=(
            ModelRiskThresholds(medium=0.001, high=0.006, critical=0.02)
            if with_thresholds
            else None
        ),
    )
    if with_sidecar:
        (directory / "manifest.json").write_text(
            json.dumps(
                {
                    "manifest": {
                        "source": "ibm-aml",
                        "row_count": 12,
                        "label_window": "ibm-aml",
                        "snapshot_query": {"source": "ibm-aml"},
                        "content_hash": "c" * 64,
                    },
                    "seed": 1729,
                    "rows": 12,
                }
            ),
            encoding="utf-8",
        )
    return directory


def test_discover_skips_fixture_failed_corrupt_and_sidecarless(tmp_path: Path) -> None:
    (tmp_path / "v0-fixture").mkdir()  # never promoted even if present under the root
    _write_bundle(tmp_path, "xgb-ibm-aml-fs2-failed", gates_passed=0.0, pr_auc=0.9)
    _write_bundle(
        tmp_path, "xgb-ibm-aml-fs2-nosidecar", gates_passed=1.0, pr_auc=0.9, with_sidecar=False
    )
    corrupt = _write_bundle(tmp_path, "xgb-ibm-aml-fs2-corrupt", gates_passed=1.0, pr_auc=0.9)
    (corrupt / "model.json").write_text("garbage", encoding="utf-8")
    good = _write_bundle(tmp_path, "xgb-ibm-aml-fs2-good", gates_passed=1.0, pr_auc=0.2)

    bundles = discover_bundles(tmp_path, label=None)
    assert [bundle.directory for bundle in bundles] == [good]
    assert bundles[0].metadata.risk_thresholds is not None


def test_discover_with_label_filters_to_that_bundle(tmp_path: Path) -> None:
    _write_bundle(tmp_path, "xgb-a", gates_passed=1.0, pr_auc=0.2)
    _write_bundle(tmp_path, "xgb-b", gates_passed=1.0, pr_auc=0.9)
    bundles = discover_bundles(tmp_path, label="xgb-a")
    assert len(bundles) == 1
    assert bundles[0].metadata.version_label == "xgb-a"


def test_discover_empty_root_returns_nothing(tmp_path: Path) -> None:
    assert discover_bundles(tmp_path / "missing", label=None) == []


async def test_register_and_promote_flip_the_pointer_through_the_lifecycle(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    await seed(db_session)  # fixture model ACTIVE + deployment + demo admin
    bundle = discover_bundles(
        _write_bundle(tmp_path, "xgb-ibm-aml-fs2-promote", gates_passed=1.0, pr_auc=0.2).parent,
        label=None,
    )[0]

    version = await register_bundle(db_session, bundle)
    assert version.status is ModelVersionStatus.CANDIDATE
    outcome = await promote_to_active(db_session, version, bundle)
    await _update_default_model_config(db_session, version.version_label)
    await db_session.commit()

    assert "activated" in outcome
    assert version.status is ModelVersionStatus.ACTIVE
    assert version.approved_by is not None  # the human-gate audit trail is stamped
    deployment = (await db_session.execute(select(ModelDeployment).limit(1))).scalar_one()
    assert deployment.active_version_id == version.id
    fixture = (
        await db_session.execute(
            select(ModelVersion).where(ModelVersion.version_label == "v0-fixture")
        )
    ).scalar_one()
    assert fixture.status is ModelVersionStatus.ARCHIVED
    assert deployment.previous_active_version_id == fixture.id  # rollback stays possible
    config = (
        await db_session.execute(
            select(SystemConfig).where(
                SystemConfig.agency_id.is_(None), SystemConfig.key == "defaultModelId"
            )
        )
    ).scalar_one()
    assert config.value == version.version_label

    # Idempotency: registering the same bundle again reuses the row; re-promoting is a no-op.
    again = await register_bundle(db_session, bundle)
    assert again.id == version.id
    assert "already the active model" in await promote_to_active(db_session, again, bundle)
