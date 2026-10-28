"""Summary: Typed inputs and aggregate evidence for the ranking-policy study.

Key classes:
- StudyRun: immutable source/model/fold binding for one comparison.
- StudyConfig: predeclared candidates, review budget, and runtime policy.
- RankingMetrics: exact-budget detection and boundary-tie accounting.
- Cohort: in-memory arrays; account identities never enter published reports.

Key functions:
- load_config: load the non-secret, frozen study specification.

Notes:
- Retrospective results are explicitly exploratory, including the served model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from fraudlens_core import RiskPolicy


class StudyRun(BaseModel):
    """One source and frozen bundle, independently hash-bound."""

    name: str = Field(..., description="Stable comparison identity.")
    candidate: str = Field(..., description="Source identity in config/fulldata.yaml.")
    work_dir: str = Field(..., description="Ignored directory holding reconstructed inputs.")
    model_dir: str = Field(..., description="Frozen model bundle directory.")
    metadata_sha256: str = Field(
        ..., pattern=r"^[a-f0-9]{64}$", description="Frozen metadata hash."
    )
    split: Literal["temporal", "account_whole"] = Field(..., description="Original fold protocol.")
    served: bool = Field(
        ..., description="Whether this bundle matches the observed active pointer."
    )
    reference_recall: float | None = Field(
        default=None, description="Historical same-source metric to audit without overwriting it."
    )
    training_end_epoch: int | None = Field(
        default=None,
        description="Latest actual training transaction; later rows form a time slice.",
    )


class StudyConfig(BaseModel):
    """Predeclared comparison parameters; no secrets or cloud operation."""

    model_config = ConfigDict(extra="forbid")
    weights: list[float] = Field(..., description="Model shares, including model-only at one.")
    budget: float = Field(..., gt=0, lt=1, description="Exact top-ceil review fraction.")
    seed: int = Field(..., description="Label-independent boundary-tie seed.")
    batch_rows: int = Field(..., gt=0, description="Bounded prediction batch size.")
    policy: RiskPolicy = Field(..., description="Observed runtime baseline, retained unchanged.")
    runs: list[StudyRun] = Field(..., description="Source/model comparisons fixed before scoring.")
    output_dir: str = Field(..., description="Ignored prediction-cache and report directory.")
    replay_source: str = Field(..., description="Frozen original preprocessing source attachment.")
    replay_sha256: str = Field(
        ..., pattern=r"^[a-f0-9]{64}$", description="Original replay source hash."
    )

    @model_validator(mode="after")
    def valid_weights(self) -> StudyConfig:
        """Reject duplicate or invalid candidate weights before looking at labels."""
        if len(set(self.weights)) != len(self.weights) or any(not 0 < w <= 1 for w in self.weights):
            raise ValueError("weights must be unique inside (0,1]")
        if self.policy.model_weight not in self.weights or 1.0 not in self.weights:
            raise ValueError("include the baseline and model-only candidate")
        return self


class RankingMetrics(BaseModel):
    """Detection counts and tie sensitivity at one realized operating point."""

    rows: int = Field(..., ge=0, description="Evaluated transactions.")
    positives: int = Field(..., ge=0, description="Positive transaction labels.")
    alerts: int = Field(..., ge=0, description="Actual review count.")
    true_positives: int = Field(..., ge=0, description="Detected positive labels.")
    false_positives: int = Field(..., ge=0, description="Selected negative labels.")
    recall: float = Field(..., description="Detected fraction of positives; zero for no positives.")
    precision: float = Field(..., description="Positive fraction of alerts; zero for no alerts.")
    cutoff: float = Field(..., description="Score cutoff.")
    boundary_ties: int = Field(..., description="Rows at a fixed-budget boundary.")
    selected_ties: int = Field(..., description="Budget slots available within that tie.")
    expected_recall: float = Field(..., description="Recall averaged over uniform boundary ties.")


class Cohort(BaseModel):
    """Validated local arrays; never serialized into a public artifact."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    row_id: Any = Field(..., description="Synthetic source row positions, local only.")
    label: Any = Field(..., description="Binary transaction labels.")
    probability: Any = Field(..., description="Unmodified calibrated model probabilities.")
    rules: Any = Field(..., description="Canonical weighted rule subscore.")
    epoch: Any = Field(..., description="UTC transaction epoch seconds.")
    holdout: Any = Field(..., description="Boolean fold selector.")

    @model_validator(mode="after")
    def valid_arrays(self) -> Cohort:
        """Require aligned finite arrays and genuine disjoint evaluation folds."""
        arrays = [np.asarray(getattr(self, name)) for name in type(self).model_fields]
        if not arrays[0].size or any(a.ndim != 1 or a.size != arrays[0].size for a in arrays):
            raise ValueError("cohort arrays must be nonempty aligned vectors")
        if not np.isin(self.label, [0, 1]).all() or not np.isin(self.holdout, [False, True]).all():
            raise ValueError("labels and fold selector must be binary")
        for name in ("probability", "rules"):
            array = np.asarray(getattr(self, name))
            if not (np.isfinite(array).all() and ((array >= 0) & (array <= 1)).all()):
                raise ValueError("scores must be finite inside [0,1]")
        return self


def load_config(path: Path) -> StudyConfig:
    """Parse the frozen YAML through a strict Pydantic configuration boundary."""
    return StudyConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
