"""Summary: Frozen, typed configuration for the IBM full-data training protocol.

Key classes:
- FullDataDataset: one IBM file and pre-registered candidate identity.
- UsableRules: fail-closed source-row rejection policy.
- FoldFractions: exact outer temporal fractions.
- CalibrationSplit: exact tuning/calibration fractions.
- FeatureConfig: live-parity feature pins.
- TrainingConfig: XGBoost and streaming pins.
- PilotConfig: approved pilot row targets.
- FullDataPaths: constrained local IO paths.
- BudgetHook: Azure CPU allocation identity.
- FullDataConfig: validated datasets, temporal protocol, training pins, paths, and gates.

Key functions:
- load_fulldata_config: parse the committed YAML and bind its SHA-256.
- resolve_repo_path: anchor a validated relative configuration path at the repository.

Notes:
- Full-data inputs and outputs are constrained to gitignored .local directories.
"""

from __future__ import annotations

import hashlib
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fraudlens_ml.scoring import ModelGates

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FULLDATA_CONFIG = REPO_ROOT / "config" / "fulldata.yaml"
_LOCAL_ROOT = ".local"
_MIN_LOCAL_PATH_PARTS = 2
_UNIT = Fraction(1)

CandidateName = Literal["hi-small", "hi-medium", "li-medium"]
DatasetSource = Literal["ibm-aml", "ibm-aml-hi-medium", "ibm-aml-li-medium"]


def _fraction(value: str, field_name: str) -> Fraction:
    """Parse an exact rational string with a field-qualified error."""
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"{field_name} must be an exact rational, got '{value}'") from exc


def _local_path(value: str) -> str:
    """Require a relative named path below the gitignored .local root."""
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or len(path.parts) < _MIN_LOCAL_PATH_PARTS
        or path.parts[0] != _LOCAL_ROOT
    ):
        raise ValueError("full-data paths must be named relative paths below .local/")
    return value


class FullDataDataset(BaseModel):
    """One immutable IBM source file and its pre-registered candidate identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate: CandidateName = Field(..., description="Stable training-candidate name.")
    source: DatasetSource = Field(..., description="Fetch-registry source identifier.")
    file: str = Field(..., min_length=1, description="Expected local IBM CSV filename.")
    sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$", description="Expected file SHA-256.")
    rows_expected: int = Field(..., gt=0, description="Expected CSV data-row count.")


class UsableRules(BaseModel):
    """Fail-closed row usability policy for the IBM input boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    drop_unparsable_timestamp: Literal[True] = Field(..., description="Reject bad timestamps.")
    drop_non_positive_amount: Literal[True] = Field(..., description="Reject amounts <= 0.")
    drop_unknown_currency: Literal[True] = Field(..., description="Reject unpinned currencies.")
    drop_missing_account: Literal[True] = Field(..., description="Reject missing account keys.")
    usd_rates_file: str = Field(..., description="YAML containing the frozen usd_rates table.")


class FoldFractions(BaseModel):
    """Exact train/calibration/holdout chronological fractions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    train: str = Field(..., description="Exact train fraction.")
    calibration: str = Field(..., description="Exact calibration fraction.")
    holdout: str = Field(..., description="Exact holdout fraction.")

    @model_validator(mode="after")
    def _valid_fractions(self) -> FoldFractions:
        values = tuple(
            _fraction(getattr(self, name), f"folds.{name}") for name in type(self).model_fields
        )
        if any(value <= 0 or value >= _UNIT for value in values) or sum(values) != _UNIT:
            raise ValueError("fold fractions must each be in (0, 1) and sum exactly to 1")
        return self

    def exact(self) -> tuple[Fraction, Fraction, Fraction]:
        """Return train/calibration/holdout as exact Fraction values."""
        return tuple(  # type: ignore[return-value]
            Fraction(getattr(self, name)) for name in type(self).model_fields
        )


class CalibrationSplit(BaseModel):
    """Exact chronological split of the middle fold into tuning and calibration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tuning: str = Field(..., description="Exact tuning share of the middle fold.")
    calibration: str = Field(..., description="Exact calibration share of the middle fold.")

    @model_validator(mode="after")
    def _valid_fractions(self) -> CalibrationSplit:
        values = (
            _fraction(self.tuning, "calibration_split.tuning"),
            _fraction(self.calibration, "calibration_split.calibration"),
        )
        if any(value <= 0 or value >= _UNIT for value in values) or sum(values) != _UNIT:
            raise ValueError("calibration split must contain positive fractions summing to 1")
        return self


class FeatureConfig(BaseModel):
    """Pins that make offline feature semantics match the live scorer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    spec_version: Literal[2] = Field(..., description="Required live feature-spec version.")
    window_hours: Literal[24] = Field(..., description="Half-open history window in hours.")
    history_max: int = Field(..., gt=0, description="Most-recent event cap per account window.")


class TrainingConfig(BaseModel):
    """Fixed XGBoost, baseline, checkpoint, and streaming settings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_trees: int = Field(..., gt=0, description="Maximum boosting rounds.")
    max_depth: int = Field(..., gt=0, description="Maximum tree depth.")
    learning_rate: float = Field(..., gt=0.0, le=1.0, description="Boosting shrinkage.")
    subsample: float = Field(..., gt=0.0, le=1.0, description="Per-tree row sample share.")
    colsample_bytree: float = Field(..., gt=0.0, le=1.0, description="Per-tree feature share.")
    min_child_weight: float = Field(..., ge=0.0, description="Minimum child Hessian weight.")
    tree_method: Literal["hist"] = Field(..., description="Memory-bounded histogram builder.")
    max_bin: int = Field(..., gt=1, description="Histogram/quantile bin count.")
    early_stopping_rounds: int = Field(..., gt=0, description="Tuning rounds without improvement.")
    eval_metric: Literal["aucpr"] = Field(..., description="XGBoost tuning metric.")
    threads: Literal["auto"] | int = Field(..., description="Worker threads or auto detection.")
    seed: int = Field(..., ge=0, description="Deterministic training seed.")
    seed_per_iteration: Literal[True] = Field(
        ..., description="Make resumed boosting identical to uninterrupted training."
    )
    imbalance_strategy: Literal["scale_pos_weight"] = Field(..., description="Rare-event strategy.")
    checkpoint_every_trees: int = Field(..., gt=0, description="Checkpoint interval in trees.")
    baseline_sample_rows: int = Field(..., gt=0, description="Maximum stratified LR rows.")
    parquet_batch_rows: int = Field(..., gt=0, description="Arrow rows per streamed batch.")
    shap_background_rows: int = Field(..., gt=0, description="Training rows in SHAP background.")


class PilotConfig(BaseModel):
    """Permitted local/Azure pilot row counts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    row_targets: tuple[int, ...] = Field(..., min_length=1, description="Approved pilot sizes.")

    @field_validator("row_targets")
    @classmethod
    def _positive_unique(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        if any(value <= 0 for value in values) or tuple(sorted(set(values))) != values:
            raise ValueError("pilot row targets must be positive, unique, and increasing")
        return values


class FullDataPaths(BaseModel):
    """Gitignored input, work, and model-artifact directories."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    data_dir: str = Field(..., description="Local IBM CSV directory.")
    work_dir: str = Field(..., description="Checkpoint and intermediate-data directory.")
    artifacts_dir: str = Field(..., description="Candidate model-bundle directory.")

    _validate_paths = field_validator("data_dir", "work_dir", "artifacts_dir")(_local_path)


class BudgetHook(BaseModel):
    """Budget allocation used when projecting a paid full-data run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    allocation: Literal["azure_cpu_batch"] = Field(..., description="Budget-policy allocation.")


class FullDataConfig(BaseModel):
    """Complete immutable Phase-5 full-data protocol."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    datasets: tuple[FullDataDataset, ...] = Field(..., min_length=1, description="IBM sources.")
    usable_rules: UsableRules = Field(..., description="Input rejection policy.")
    namespace_accounts_by_source: Literal[True] = Field(..., description="Cross-file key guard.")
    folds: FoldFractions = Field(..., description="Outer temporal folds.")
    calibration_split: CalibrationSplit = Field(..., description="Middle-fold split.")
    features: FeatureConfig = Field(..., description="Feature parity pins.")
    training: TrainingConfig = Field(..., description="Fixed training protocol.")
    thresholds_from: Literal["calibration"] = Field(..., description="Threshold derivation fold.")
    candidates: tuple[CandidateName, ...] = Field(..., description="Pre-registered candidates.")
    application_candidate: CandidateName = Field(..., description="Pre-registered app candidate.")
    gates: ModelGates = Field(..., description="Shared model-promotion gates.")
    pilot: PilotConfig = Field(..., description="Pilot workload sizes.")
    paths: FullDataPaths = Field(..., description="Gitignored pipeline paths.")
    budget: BudgetHook = Field(..., description="Experiment budget hook.")
    config_sha256: str = Field(default="", exclude=True, description="Loaded config byte hash.")

    @model_validator(mode="after")
    def _candidate_contract(self) -> FullDataConfig:
        dataset_candidates = tuple(dataset.candidate for dataset in self.datasets)
        if dataset_candidates != self.candidates or len(set(self.candidates)) != len(
            self.candidates
        ):
            raise ValueError("datasets and candidates must have the same unique ordered identities")
        if self.application_candidate not in self.candidates:
            raise ValueError("application_candidate must name a configured candidate")
        if len({dataset.source for dataset in self.datasets}) != len(self.datasets):
            raise ValueError("dataset sources must be unique")
        return self

    def dataset(self, candidate_or_source: str) -> FullDataDataset:
        """Resolve a candidate name or fetch-registry source."""
        for dataset in self.datasets:
            if candidate_or_source in {dataset.candidate, dataset.source}:
                return dataset
        raise KeyError(f"unknown full-data candidate/source '{candidate_or_source}'")


def resolve_repo_path(path: str | Path) -> Path:
    """Anchor a repository-relative path while preserving explicit absolute test paths."""
    candidate = Path(path)
    return candidate if candidate.is_absolute() else REPO_ROOT / candidate


def load_fulldata_config(path: Path = DEFAULT_FULLDATA_CONFIG) -> FullDataConfig:
    """Parse a frozen config and attach the exact source-byte SHA-256."""
    raw = path.read_bytes()
    payload: Any = yaml.safe_load(raw)
    config = FullDataConfig.model_validate(payload)
    rates_path = resolve_repo_path(config.usable_rules.usd_rates_file)
    rates_payload: Any = yaml.safe_load(rates_path.read_text(encoding="utf-8"))
    if not isinstance(rates_payload, dict) or not isinstance(rates_payload.get("usd_rates"), dict):
        raise ValueError("usable_rules.usd_rates_file must contain a non-empty usd_rates mapping")
    return config.model_copy(update={"config_sha256": hashlib.sha256(raw).hexdigest()})
