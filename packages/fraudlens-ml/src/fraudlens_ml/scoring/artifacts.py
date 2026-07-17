"""Summary: Model-artifact persistence + the active-pointer-keyed loader with last-known-good
fallback (plan §16 Phase 5, §10.6). An artifact is a self-describing directory bundle: the
XGBoost booster (`model.json`) plus `metadata.json` (the feature spec, the Platt calibration
params, a SHAP background sample, the holdout metrics, and a SHA-256 of the booster file).
`load_artifact` verifies that checksum so a corrupt/truncated booster fails loudly instead of
serving silent bad scores. `ModelCache` resolves the registry pointer the backend hands it
(active version + uri, and the previous active for rollback): it lazily loads + caches the
active artifact by version label, reloads on a pointer flip, and — when the active artifact is
missing/corrupt — falls back to the previous (last-known-good) version rather than a hard
outage; if neither loads it raises, so `/readyz` can fail closed (plan §10.6). It touches only
the filesystem under a base dir (Blob/local-FS is the backend's concern) — never the DB.

Key classes:
- ArtifactError: raised when an artifact is missing, corrupt, or fails its checksum.
- Calibration: the Platt (sigmoid) mapping from raw model margin to a probability in [0,1].
- ModelArtifactMetadata: the persisted, non-binary artifact metadata (the model.json sidecar).
- LoadedArtifact: an in-memory artifact — the live booster + spec + calibration + background.
- DeploymentPointer: the active (+ previous, for rollback) version labels and artifact uris.
- ModelCache: pointer-keyed artifact cache with reload-on-flip + last-known-good fallback.

Key functions:
- save_artifact: write a booster + metadata bundle to a directory (returns the metadata).
- load_artifact: load + checksum-verify a bundle directory into a LoadedArtifact.

Notes:
- Calibration is stored, not re-fit at load, so scoring is reproducible; `apply` maps the
  booster's raw margin (log-odds) to a calibrated probability (plan §10.5.1 calibration gate).
- The SHAP background is persisted as plain JSON rows (no binary blob) so the bundle stays
  diff-friendly and checksum-stable across platforms; the explainer loads it as an ndarray.
- The cache keys by version label, so a rollback to a previously loaded version is served warm
  with no reload (caching as a fallback buffer, plan §10.6).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import xgboost as xgb
from pydantic import BaseModel, ConfigDict, Field

from fraudlens_core import ModelRiskThresholds
from fraudlens_ml.scoring.features import FeatureSpec

_MODEL_FILE = "model.json"
_METADATA_FILE = "metadata.json"


class ArtifactError(RuntimeError):
    """Raised when an artifact is missing, corrupt, or fails its checksum verification."""


class Calibration(BaseModel):
    """The Platt (sigmoid) mapping from a raw model margin to a probability in [0, 1]."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: Literal["platt"] = Field(default="platt", description="Calibration method tag.")
    a: float = Field(..., description="Platt slope applied to the raw margin (log-odds).")
    b: float = Field(..., description="Platt intercept applied to the raw margin.")

    def apply(self, margin: np.ndarray) -> np.ndarray:
        """Map raw booster margins to calibrated probabilities via the fitted sigmoid."""
        return 1.0 / (1.0 + np.exp(-(self.a * margin + self.b)))


class ModelArtifactMetadata(BaseModel):
    """The persisted, non-binary artifact metadata (the model.json sidecar)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version_label: str = Field(..., description="Unique registry version label.")
    feature_spec: FeatureSpec = Field(..., description="The ordered feature space.")
    calibration: Calibration = Field(..., description="Raw-margin -> probability calibration.")
    model_sha256: str = Field(..., description="SHA-256 of the booster file (corruption guard).")
    background: list[list[float]] = Field(
        ..., description="SHAP interventional background rows (feature-space, PHI-free)."
    )
    metrics: dict[str, float] = Field(
        default_factory=dict, description="Holdout metrics recorded at training time."
    )
    risk_thresholds: ModelRiskThresholds | None = Field(
        default=None,
        description="Holdout-derived calibrated-probability operating points for risk banding; "
        "None (legacy/synthetic artifacts) keeps the identity banding behavior.",
    )


@dataclass(frozen=True)
class LoadedArtifact:
    """An in-memory artifact: the live booster plus its spec, calibration, and background."""

    version_label: str
    booster: xgb.Booster
    feature_spec: FeatureSpec
    calibration: Calibration
    background: np.ndarray
    metrics: dict[str, float]
    risk_thresholds: ModelRiskThresholds | None = None


class DeploymentPointer(BaseModel):
    """The active (+ previous, for rollback) model version labels and artifact uris."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    active_version_label: str = Field(..., description="The active model's version label.")
    active_artifact_uri: str = Field(..., description="The active model's artifact uri (relative).")
    previous_version_label: str | None = Field(
        default=None, description="The prior active label, served as last-known-good on failure."
    )
    previous_artifact_uri: str | None = Field(
        default=None, description="The prior active artifact uri (last-known-good)."
    )


def _sha256_file(path: Path) -> str:
    """Return the hex SHA-256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_artifact(  # noqa: PLR0913 - an artifact bundles many parts; all are keyword-only
    directory: Path,
    booster: xgb.Booster,
    *,
    version_label: str,
    feature_spec: FeatureSpec,
    calibration: Calibration,
    background: np.ndarray,
    metrics: dict[str, float],
    risk_thresholds: ModelRiskThresholds | None = None,
) -> ModelArtifactMetadata:
    """Write a booster + metadata bundle to a directory and return the recorded metadata."""
    directory.mkdir(parents=True, exist_ok=True)
    model_path = directory / _MODEL_FILE
    booster.save_model(str(model_path))
    metadata = ModelArtifactMetadata(
        version_label=version_label,
        feature_spec=feature_spec,
        calibration=calibration,
        model_sha256=_sha256_file(model_path),
        background=[[float(value) for value in row] for row in np.asarray(background)],
        metrics=metrics,
        risk_thresholds=risk_thresholds,
    )
    (directory / _METADATA_FILE).write_text(
        json.dumps(metadata.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return metadata


def load_artifact(directory: Path) -> LoadedArtifact:
    """Load + checksum-verify a bundle directory into a LoadedArtifact (raises on any fault)."""
    model_path = directory / _MODEL_FILE
    metadata_path = directory / _METADATA_FILE
    if not model_path.is_file() or not metadata_path.is_file():
        raise ArtifactError(f"artifact bundle incomplete at {directory}")
    try:
        metadata = ModelArtifactMetadata.model_validate_json(metadata_path.read_text("utf-8"))
    except ValueError as exc:
        raise ArtifactError(f"artifact metadata invalid at {directory}") from exc
    if _sha256_file(model_path) != metadata.model_sha256:
        raise ArtifactError(f"artifact checksum mismatch at {directory} (corrupt booster)")
    booster = xgb.Booster()
    booster.load_model(str(model_path))
    return LoadedArtifact(
        version_label=metadata.version_label,
        booster=booster,
        feature_spec=metadata.feature_spec,
        calibration=metadata.calibration,
        background=np.array(metadata.background, dtype=np.float64),
        metrics=metadata.metrics,
        risk_thresholds=metadata.risk_thresholds,
    )


class ModelCache:
    """Pointer-keyed artifact cache with reload-on-flip and last-known-good fallback."""

    def __init__(self, base_dir: Path) -> None:
        """Bind the base directory under which artifact uris are resolved."""
        self._base_dir = base_dir
        self._cache: dict[str, LoadedArtifact] = {}

    def _load_cached(self, version_label: str, artifact_uri: str) -> LoadedArtifact | None:
        """Return the cached artifact for a label, loading+caching on miss; None on failure."""
        cached = self._cache.get(version_label)
        if cached is not None:
            return cached
        try:
            loaded = load_artifact(self._base_dir / artifact_uri)
        except ArtifactError:
            return None
        self._cache[version_label] = loaded
        return loaded

    def get(self, pointer: DeploymentPointer) -> LoadedArtifact:
        """Resolve the active artifact, falling back to the last-known-good previous version."""
        active = self._load_cached(pointer.active_version_label, pointer.active_artifact_uri)
        if active is not None:
            return active
        if pointer.previous_version_label and pointer.previous_artifact_uri:
            previous = self._load_cached(
                pointer.previous_version_label, pointer.previous_artifact_uri
            )
            if previous is not None:
                return previous
        raise ArtifactError(
            f"no loadable model artifact for active '{pointer.active_version_label}' "
            "or its last-known-good fallback"
        )
