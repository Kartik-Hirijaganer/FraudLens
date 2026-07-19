"""Summary: Frozen result-report models for the offline GFP tenant-isolation study
(GFP plan Phase 3 boundaries; consumed by the Phase-5 orchestrator and the Phase-6
curation step). `ArmMetrics` carries one arm x scope x dataset evaluation on the frozen
metric contract (raw PR-AUC + normalized mean-lift, ROC-AUC, Brier/ECE, top-k operating
points, calibration-selected minority F1); `ScopeComparison` carries the signed isolation
arithmetic (`isolationDelta = global - perTenant`, lost/retained graph lift with a null
denominator guard); `StudyReport` is the full `study.json` payload — always
`servingEligible=false` (ADR-017), engine-stamped, and free of models/predictions/IDs/paths.

Key classes:
- TopKMetrics: precision/recall/captured-positives at one review-budget fraction.
- HoldoutSummary: holdout composition (positives, negatives, illicit ratio).
- ArmMetrics: one arm x scope x dataset evaluation on the frozen metric contract.
- PairedDeltaInterval: paired bootstrap 95% interval for a PR-AUC delta.
- ArmDelta: one arm-to-arm PR-AUC delta with its paired interval.
- ScopeComparison: per-dataset global-vs-per-tenant isolation arithmetic (signed).
- StudyReport: the complete typed study.json payload (servingEligible always false).

Key functions:
- (none)

Notes:
- Serialized with camelCase aliases (FraudLens casing rule for published artifacts).
- retained_graph_lift is None whenever the global lift denominator is not positive; the
  neutral wording rule (plan: "isolation delta", not "cost") is enforced by rendering,
  which must consult `isolation_delta`'s SIGN, never assume it.
- No file paths, account tokens, per-row predictions, or model binaries may appear here;
  Phase-8 redaction tests enforce this on the committed artifact.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from lib.gfp.boundaries import DatasetProvenance

_REPORT_MODEL_CONFIG = ConfigDict(
    frozen=True, extra="forbid", alias_generator=to_camel, populate_by_name=True
)

Arm = Literal["A", "B", "C"]
Scope = Literal["shared", "global", "per_tenant"]


class TopKMetrics(BaseModel):
    """Operating point at one review-budget fraction of the holdout."""

    model_config = _REPORT_MODEL_CONFIG

    fraction: float = Field(..., gt=0.0, lt=1.0, description="Reviewed share, e.g. 0.001.")
    precision: float = Field(..., ge=0.0, le=1.0, description="Precision at the budget.")
    recall: float = Field(..., ge=0.0, le=1.0, description="Recall at the budget.")
    captured_positives: int = Field(..., ge=0, description="Positives inside the budget.")


class HoldoutSummary(BaseModel):
    """Holdout composition the metrics are computed on."""

    model_config = _REPORT_MODEL_CONFIG

    positives: int = Field(..., ge=0, description="Illicit rows in the holdout targets.")
    negatives: int = Field(..., ge=0, description="Licit rows in the holdout targets.")
    illicit_ratio: float = Field(..., ge=0.0, le=1.0, description="positives / total.")


class ArmMetrics(BaseModel):
    """One arm x scope x dataset evaluation on the frozen metric contract."""

    model_config = _REPORT_MODEL_CONFIG

    dataset_source: str = Field(..., min_length=1, description="Fetch-registry source id.")
    arm: Arm = Field(
        ..., description="A = 19 served features; B = +fan/degree/vertex; C = B + multi-hop."
    )
    scope: Scope = Field(
        ...,
        description="'shared' for Arm A (trained once), else the graph scope the arm saw.",
    )
    holdout: HoldoutSummary = Field(..., description="Holdout composition.")
    pr_auc: float = Field(..., ge=0.0, le=1.0, description="Raw holdout PR-AUC.")
    pr_auc_normalized: float = Field(
        ..., ge=0.0, description="Mean-lift PR-AUC / illicit ratio (base-rate normalized)."
    )
    roc_auc: float = Field(..., ge=0.0, le=1.0, description="Holdout ROC-AUC (secondary).")
    brier: float = Field(..., ge=0.0, le=1.0, description="Brier score of calibrated probs.")
    ece: float = Field(..., ge=0.0, le=1.0, description="Expected calibration error.")
    top_k: tuple[TopKMetrics, ...] = Field(
        ..., min_length=1, description="Operating points at the contract's review budgets."
    )
    minority_f1: float = Field(
        ..., ge=0.0, le=1.0, description="Minority-class F1 at the calibration-picked threshold."
    )
    minority_f1_threshold: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Threshold selected on CALIBRATION only, applied once to holdout.",
    )

    @model_validator(mode="after")
    def _arm_a_is_shared(self) -> ArmMetrics:
        """Arm A is trained once and shared across scopes; graph arms carry a real scope."""
        if self.arm == "A" and self.scope != "shared":
            raise ValueError("Arm A is trained once — its scope must be 'shared'")
        if self.arm != "A" and self.scope == "shared":
            raise ValueError(f"Arm {self.arm} must be scoped 'global' or 'per_tenant'")
        return self


class PairedDeltaInterval(BaseModel):
    """Paired stratified-bootstrap 95% interval for a PR-AUC delta."""

    model_config = _REPORT_MODEL_CONFIG

    lower: float = Field(..., description="2.5th percentile of the paired delta.")
    upper: float = Field(..., description="97.5th percentile of the paired delta.")
    replicates: int = Field(..., gt=0, description="Bootstrap replicates (contract: 200).")
    holdout_subset_cap: int = Field(
        ...,
        gt=0,
        description="Fixed holdout-subset cap the replicates were drawn from (contract: 250k).",
    )

    @model_validator(mode="after")
    def _ordered(self) -> PairedDeltaInterval:
        """The interval cannot be inverted."""
        if self.lower > self.upper:
            raise ValueError(f"interval lower {self.lower} exceeds upper {self.upper}")
        return self


class ArmDelta(BaseModel):
    """One arm-to-arm PR-AUC delta with its paired interval."""

    model_config = _REPORT_MODEL_CONFIG

    dataset_source: str = Field(..., min_length=1, description="Fetch-registry source id.")
    from_arm: Arm = Field(..., description="Baseline arm.")
    to_arm: Arm = Field(..., description="Comparison arm.")
    scope: Scope = Field(..., description="Graph scope of the comparison arm.")
    pr_auc_delta: float = Field(..., description="to_arm PR-AUC minus from_arm PR-AUC.")
    interval: PairedDeltaInterval = Field(..., description="Paired 95% bootstrap interval.")


class ScopeComparison(BaseModel):
    """Per-dataset signed isolation arithmetic between the global and per-tenant scopes."""

    model_config = _REPORT_MODEL_CONFIG

    dataset_source: str = Field(..., min_length=1, description="Fetch-registry source id.")
    isolation_delta_b: float = Field(
        ..., description="Arm B: global PR-AUC minus per-tenant PR-AUC (SIGNED)."
    )
    isolation_delta_c: float = Field(
        ..., description="Arm C: global PR-AUC minus per-tenant PR-AUC (SIGNED)."
    )
    lost_graph_lift: float = Field(
        ..., description="(C_global - A) - (C_perTenant - A): graph lift lost to isolation."
    )
    retained_graph_lift: float | None = Field(
        default=None,
        description="(C_perTenant - A) / (C_global - A); None unless the global lift is > 0.",
    )
    retained_lift_note: str | None = Field(
        default=None,
        description="Required explanation when retained_graph_lift is None (null denominator).",
    )

    @model_validator(mode="after")
    def _null_denominator_documented(self) -> ScopeComparison:
        """A null retained lift must say why; a present one must not carry the note."""
        if self.retained_graph_lift is None and not self.retained_lift_note:
            raise ValueError("retained_graph_lift=None requires retained_lift_note")
        if self.retained_graph_lift is not None and self.retained_lift_note:
            raise ValueError("retained_lift_note is only for the null-denominator case")
        return self


class StudyReport(BaseModel):
    """The complete typed study.json payload for one benchmark run (never serving-eligible)."""

    model_config = _REPORT_MODEL_CONFIG

    run_id: str = Field(..., min_length=1, description="Deterministic id of this run.")
    engine_name: Literal["snapml", "reference", "fake"] = Field(
        ..., description="Graph engine used; ONLY 'snapml' runs may be published (Phase 4)."
    )
    engine_version: str = Field(..., min_length=1, description="Engine version string.")
    library_versions: dict[str, str] = Field(
        ..., description="Pinned library versions observed at run time (name -> version)."
    )
    config_sha256: str = Field(
        ..., min_length=64, max_length=64, description="SHA-256 of config/gfp-benchmark.yaml."
    )
    seed: int = Field(..., ge=0, description="The run's deterministic seed (contract: 1729).")
    datasets: tuple[DatasetProvenance, ...] = Field(
        ..., min_length=1, description="Per-dataset file + sampling provenance."
    )
    arm_feature_counts: dict[str, int] = Field(
        ..., description="Feature-vector width per arm name (A/B/C)."
    )
    graph_feature_names: tuple[str, ...] = Field(
        ...,
        min_length=1,
        description=(
            "Engineered GFP feature names in engine output order (Arm B block then the "
            "Arm C increment); Arm A's 19 served names are pinned by the feature spec."
        ),
    )
    metrics: tuple[ArmMetrics, ...] = Field(..., min_length=1, description="All evaluations.")
    deltas: tuple[ArmDelta, ...] = Field(..., description="Arm-to-arm deltas with intervals.")
    comparisons: tuple[ScopeComparison, ...] = Field(
        ..., description="Signed isolation arithmetic per dataset."
    )
    serving_eligible: Literal[False] = Field(
        ..., description="Always false: no scope of GFP features may serve (ADR-017)."
    )
    notes: tuple[str, ...] = Field(
        default=(),
        description="Disclosures (batch-causality caveat, sampling bias, subset caveat).",
    )
