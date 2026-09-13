"""Summary: Typed manifests for checkpointed full-data training and evaluation.

Key classes:
- CandidateEvaluation: one source's model, baseline, gates, and calibration thresholds.
- SourceRunManifest: reconciliation, folds, prevalence, and candidate evaluation.
- AccountOverlap: pairwise raw account-key overlap count.
- RunCost: projected and settled experiment costs.
- FullDataRunManifest: hash-bound multi-source run provenance, resources, cost, and timing.

Key functions:
- peak_rss_mib: return process peak resident memory in MiB.

Notes:
- Published projections exclude raw accounts, per-row predictions, and local file paths.
"""

from __future__ import annotations

import platform
import resource
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from fraudlens_core import ModelRiskThresholds
from fraudlens_ml.scoring import CandidateMetrics, GateReport
from lib.fulldata.folds import FoldManifest
from lib.fulldata.ingest import IngestReconciliation

_MODEL_CONFIG = ConfigDict(
    frozen=True, extra="forbid", alias_generator=to_camel, populate_by_name=True
)
_KIB_PER_MIB = 1024.0
_BYTES_PER_MIB = 1024.0 * 1024.0


class CandidateEvaluation(BaseModel):
    """Aggregate evaluation and artifact identity for one fixed candidate."""

    model_config = _MODEL_CONFIG

    candidate: str = Field(..., description="Pre-registered candidate identity.")
    source: str = Field(..., description="Fetch-registry source identity.")
    feature_spec_version: int = Field(..., ge=1, description="Trained feature-spec version.")
    model_bundle: str = Field(..., min_length=1, description="Bundle name below artifacts_dir.")
    model_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$", description="Booster byte hash.")
    completed_trees: int = Field(..., gt=0, description="Boosting rounds after early stop.")
    scale_pos_weight: float = Field(..., gt=0.0, description="Train prevalence class weight.")
    calibration_method: Literal["platt"] = Field(..., description="Probability calibration.")
    threshold_source: Literal["calibration", "holdout"] = Field(
        ..., description="Fold from which operating thresholds were derived."
    )
    risk_thresholds: ModelRiskThresholds | None = Field(
        default=None, description="Calibration-derived operating thresholds, when nondegenerate."
    )
    metrics: CandidateMetrics = Field(..., description="Final holdout metrics.")
    baseline_pr_auc: float = Field(..., ge=0.0, le=1.0, description="Capped LR baseline PR-AUC.")
    gates: GateReport = Field(..., description="Shared model-gate evaluation.")
    training_seconds: float = Field(..., ge=0.0, description="Candidate fit/evaluation duration.")
    training_peak_rss_mib: float = Field(
        ..., ge=0.0, description="Training-process peak resident memory in MiB."
    )


class SourceRunManifest(BaseModel):
    """Reconciliation, temporal split, and evaluation for one IBM source."""

    model_config = _MODEL_CONFIG

    candidate: str = Field(..., description="Pre-registered candidate identity.")
    source: str = Field(..., description="Fetch-registry source identity.")
    reconciliation: IngestReconciliation = Field(..., description="Input usability accounting.")
    folds: FoldManifest = Field(..., description="Whole-cohort temporal split provenance.")
    evaluation: CandidateEvaluation | None = Field(
        default=None, description="Candidate result; absent before training completes."
    )


class AccountOverlap(BaseModel):
    """Cross-source account-key overlap measured before independence claims."""

    model_config = _MODEL_CONFIG

    left_source: str = Field(..., description="First source identity.")
    right_source: str = Field(..., description="Second source identity.")
    overlapping_accounts: int = Field(..., ge=0, description="Shared raw bank/account pairs.")


class RunCost(BaseModel):
    """Optional projected and settled cost for a local or Azure run."""

    model_config = _MODEL_CONFIG

    allocation: str = Field(..., description="Budget allocation identity.")
    projected_usd: float | None = Field(default=None, ge=0.0, description="Admission projection.")
    actual_usd: float | None = Field(default=None, ge=0.0, description="Settled provider cost.")


class FullDataRunManifest(BaseModel):
    """Complete aggregate provenance for a resumable full-data run."""

    model_config = _MODEL_CONFIG

    run_id: str = Field(..., min_length=1, description="Hash-derived stable run identity.")
    config_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$", description="Protocol byte hash.")
    commit: str = Field(..., min_length=7, description="Source Git revision used for the run.")
    provenance: Literal["sample", "measured"] = Field(..., description="Pilot or full-data tag.")
    application_candidate: str = Field(..., description="Pre-registered application candidate.")
    started_at: str = Field(..., description="UTC ISO-8601 run start.")
    completed_at: str = Field(..., description="UTC ISO-8601 manifest completion.")
    duration_seconds: float = Field(..., ge=0.0, description="End-to-end observed duration.")
    peak_rss_mib: float = Field(
        ..., ge=0.0, description="Maximum observed stage peak resident MiB."
    )
    vm_sku: str = Field(..., min_length=1, description="Observed host/VM SKU label.")
    library_versions: dict[str, str] = Field(
        ..., description="Observed analytical library versions."
    )
    sources: tuple[SourceRunManifest, ...] = Field(..., min_length=1, description="Source results.")
    cross_file_overlap: tuple[AccountOverlap, ...] = Field(
        default=(), description="Pairwise account overlap before evidence interpretation."
    )
    stage_durations_seconds: dict[str, float] = Field(
        default_factory=dict, description="Observed duration by pipeline stage."
    )
    stage_peak_rss_mib: dict[str, float] = Field(
        default_factory=dict, description="Observed process peak resident MiB by pipeline stage."
    )
    cost: RunCost = Field(..., description="Budget projection and reconciliation.")


def peak_rss_mib() -> float:
    """Return peak resident memory in MiB across macOS and Linux resource units."""
    rss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    divisor = _BYTES_PER_MIB if platform.system() == "Darwin" else _KIB_PER_MIB
    return rss / divisor
