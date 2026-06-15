"""Phase 5 artifact tests (plan §16 Phase 5: "artifact loader cache + reload on pointer
change; missing active -> last-known-good fallback"). Verify save/load round-trips, checksum +
corruption guards, the Platt calibration mapping, and the ModelCache pointer resolution."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from fraudlens_ml.scoring import (
    ArtifactError,
    Calibration,
    DeploymentPointer,
    ModelCache,
    current_feature_spec,
    load_artifact,
    save_artifact,
)


def _copy_bundle(source: Path, dest: Path) -> Path:
    """Copy the committed fixture bundle to a fresh directory and return it."""
    shutil.copytree(source, dest)
    return dest


def test_calibration_apply_maps_margin_to_unit_interval() -> None:
    calibration = Calibration(a=1.2, b=-0.5)
    probs = calibration.apply(np.array([-5.0, 0.0, 5.0]))
    assert probs.shape == (3,)
    assert np.all((probs >= 0.0) & (probs <= 1.0))
    assert probs[0] < probs[1] < probs[2]  # monotonic in the margin


def test_load_fixture_bundle_exposes_booster_spec_and_calibration(fixture_model_dir: Path) -> None:
    loaded = load_artifact(fixture_model_dir)
    assert loaded.version_label == "v0-fixture"
    assert loaded.feature_spec == current_feature_spec()
    assert loaded.calibration.method == "platt"
    assert loaded.background.shape[1] == len(current_feature_spec().features)


def test_save_then_load_round_trips(fixture_model_dir: Path, tmp_path: Path) -> None:
    loaded = load_artifact(fixture_model_dir)
    out = tmp_path / "round-trip"
    metadata = save_artifact(
        out,
        loaded.booster,
        version_label="round-trip-1",
        feature_spec=loaded.feature_spec,
        calibration=loaded.calibration,
        background=loaded.background,
        metrics={"pr_auc": 0.61},
    )
    assert metadata.version_label == "round-trip-1"
    reloaded = load_artifact(out)
    assert reloaded.version_label == "round-trip-1"
    assert reloaded.metrics == {"pr_auc": 0.61}
    assert np.allclose(reloaded.background, loaded.background)


def test_load_missing_bundle_raises(tmp_path: Path) -> None:
    with pytest.raises(ArtifactError, match="incomplete"):
        load_artifact(tmp_path / "does-not-exist")


def test_load_invalid_metadata_raises(fixture_model_dir: Path, tmp_path: Path) -> None:
    bundle = _copy_bundle(fixture_model_dir, tmp_path / "bad-meta")
    (bundle / "metadata.json").write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(ArtifactError, match="metadata invalid"):
        load_artifact(bundle)


def test_load_checksum_mismatch_raises(fixture_model_dir: Path, tmp_path: Path) -> None:
    bundle = _copy_bundle(fixture_model_dir, tmp_path / "corrupt")
    with (bundle / "model.json").open("a", encoding="utf-8") as handle:
        handle.write("\n")  # tamper with the booster bytes -> checksum mismatch
    with pytest.raises(ArtifactError, match="checksum mismatch"):
        load_artifact(bundle)


def test_cache_loads_active_and_serves_warm(fixture_model_dir: Path, tmp_path: Path) -> None:
    _copy_bundle(fixture_model_dir, tmp_path / "active")
    cache = ModelCache(tmp_path)
    pointer = DeploymentPointer(active_version_label="active", active_artifact_uri="active")
    first = cache.get(pointer)
    second = cache.get(pointer)
    assert first is second  # cached: the same loaded instance is reused (no reload)


def test_cache_reloads_on_pointer_flip(fixture_model_dir: Path, tmp_path: Path) -> None:
    _copy_bundle(fixture_model_dir, tmp_path / "v1")
    _copy_bundle(fixture_model_dir, tmp_path / "v2")
    cache = ModelCache(tmp_path)
    first = cache.get(DeploymentPointer(active_version_label="v1", active_artifact_uri="v1"))
    flipped = cache.get(DeploymentPointer(active_version_label="v2", active_artifact_uri="v2"))
    assert first is not flipped  # a pointer flip loads the new version


def test_cache_falls_back_to_last_known_good(fixture_model_dir: Path, tmp_path: Path) -> None:
    _copy_bundle(fixture_model_dir, tmp_path / "good")
    pointer = DeploymentPointer(
        active_version_label="broken",
        active_artifact_uri="missing-dir",  # active fails to load
        previous_version_label="good",
        previous_artifact_uri="good",  # last-known-good resolves
    )
    loaded = ModelCache(tmp_path).get(pointer)
    assert loaded.version_label == "v0-fixture"  # served the previous (good) artifact


def test_cache_raises_when_neither_active_nor_previous_loads(tmp_path: Path) -> None:
    pointer = DeploymentPointer(
        active_version_label="broken",
        active_artifact_uri="nope",
        previous_version_label="also-broken",
        previous_artifact_uri="also-nope",
    )
    with pytest.raises(ArtifactError, match="no loadable model artifact"):
        ModelCache(tmp_path).get(pointer)


def test_cache_raises_when_active_fails_and_no_previous(tmp_path: Path) -> None:
    pointer = DeploymentPointer(active_version_label="broken", active_artifact_uri="nope")
    with pytest.raises(ArtifactError):
        ModelCache(tmp_path).get(pointer)
