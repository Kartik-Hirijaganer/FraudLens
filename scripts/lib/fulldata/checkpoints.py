"""Summary: Bounded XGBoost matrices and hash-bound resumable checkpoint mechanics.

Key classes:
- ParquetFoldIterator: bounded Arrow batches for one temporal fold.
- TrainingCheckpointState: early-stop state persisted beside each model checkpoint.

Key functions:
- advance_checkpoint_state: carry global tuning patience across checkpoint boundaries.
- fold_matrix: construct one streamed QuantileDMatrix from folded Parquet.
- fit_booster: train or resume deterministic XGBoost with periodic checkpoints.

Notes:
- Checkpoint state is written after model bytes and bound to the config and feature hashes.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.compute as pc
import pyarrow.parquet as pq
import xgboost as xgb
from pydantic import BaseModel, ConfigDict, Field

from fraudlens_ml.scoring import FEATURE_NAMES
from lib.fulldata.config import FullDataConfig
from lib.study import atomic_write_model


class TrainingCheckpointState(BaseModel):
    """Early-stop state required to resume with uninterrupted-run semantics."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    checkpoint_identity: str = Field(..., min_length=1, description="Config/feature binding.")
    completed_trees: int = Field(..., ge=0, description="Trees contained by the checkpoint.")
    best_score: float | None = Field(default=None, description="Best tuning AUCPR observed.")
    stale_rounds: int = Field(..., ge=0, description="Rounds since the last tuning improvement.")
    early_stopped: bool = Field(..., description="Whether patience was exhausted.")


class ParquetFoldIterator(xgb.DataIter):
    """XGBoost DataIter yielding bounded Arrow batches for one named fold."""

    def __init__(self, path: Path, fold: str, batch_rows: int) -> None:
        """Bind a Parquet file, temporal fold, and maximum in-memory batch size."""
        super().__init__(release_data=True)
        self._path = path
        self._fold = fold
        self._batch_rows = batch_rows
        self._iterator: Any = None

    def reset(self) -> None:
        """Rewind the Parquet batch iterator."""
        columns = [*FEATURE_NAMES, "label", "fold"]
        self._iterator = pq.ParquetFile(self._path).iter_batches(
            batch_size=self._batch_rows, columns=columns
        )

    def next(self, input_data: Any) -> bool:
        """Supply the next non-empty batch matching this iterator's fold."""
        if self._iterator is None:
            self.reset()
        for batch in self._iterator:
            mask = pc.equal(batch.column("fold"), self._fold)
            selected = batch.filter(mask)
            if selected.num_rows == 0:
                continue
            frame = selected.to_pandas()
            input_data(
                data=frame.loc[:, FEATURE_NAMES].to_numpy(dtype=np.float32),
                label=frame["label"].to_numpy(dtype=np.uint8),
                feature_names=list(FEATURE_NAMES),
            )
            return True
        return False


def _checkpoint_state_path(checkpoint: Path) -> Path:
    return checkpoint.with_suffix(".state.json")


def _latest_checkpoint(directory: Path) -> Path | None:
    checkpoints = sorted(
        path for path in directory.glob("model-*.ubj") if _checkpoint_state_path(path).is_file()
    )
    return checkpoints[-1] if checkpoints else None


def advance_checkpoint_state(
    state: TrainingCheckpointState, scores: list[float], patience: int
) -> TrainingCheckpointState:
    """Advance maximized AUCPR patience in iteration order and stop at the exact trigger."""
    best = state.best_score
    stale = state.stale_rounds
    consumed = 0
    stopped = False
    for score in scores:
        consumed += 1
        if best is None or score > best:
            best = score
            stale = 0
        else:
            stale += 1
        if stale >= patience:
            stopped = True
            break
    return TrainingCheckpointState(
        checkpoint_identity=state.checkpoint_identity,
        completed_trees=state.completed_trees + consumed,
        best_score=best,
        stale_rounds=stale,
        early_stopped=stopped,
    )


def _save_checkpoint(
    checkpoint_dir: Path, booster: xgb.Booster, state: TrainingCheckpointState
) -> Path:
    """Persist the model first and atomic state last so incomplete pairs are never resumed."""
    checkpoint = checkpoint_dir / f"model-{state.completed_trees:06d}.ubj"
    staging = checkpoint.with_name(f".{checkpoint.name}.staging.ubj")
    booster.save_model(staging)
    staging.replace(checkpoint)
    atomic_write_model(_checkpoint_state_path(checkpoint), state)
    return checkpoint


def _threads(config: FullDataConfig) -> int:
    value = config.training.threads
    return max(1, os.cpu_count() or 1) if value == "auto" else value


def fold_matrix(
    config: FullDataConfig, path: Path, fold: str, ref: xgb.DMatrix | None = None
) -> xgb.QuantileDMatrix:
    """Build one memory-bounded quantile matrix from a named temporal fold."""
    iterator = ParquetFoldIterator(path, fold, config.training.parquet_batch_rows)
    return xgb.QuantileDMatrix(
        iterator,
        max_bin=config.training.max_bin,
        nthread=_threads(config),
        ref=ref,
    )


def _params(config: FullDataConfig, scale_pos_weight: float) -> dict[str, Any]:
    training = config.training
    return {
        "objective": "binary:logistic",
        "eval_metric": training.eval_metric,
        "max_depth": training.max_depth,
        "eta": training.learning_rate,
        "subsample": training.subsample,
        "colsample_bytree": training.colsample_bytree,
        "min_child_weight": training.min_child_weight,
        "tree_method": training.tree_method,
        "max_bin": training.max_bin,
        "seed": training.seed,
        "seed_per_iteration": training.seed_per_iteration,
        "nthread": _threads(config),
        "scale_pos_weight": scale_pos_weight,
    }


def fit_booster(  # noqa: PLR0913 - explicit training/checkpoint contract
    config: FullDataConfig,
    train_matrix: xgb.DMatrix,
    tuning_matrix: xgb.DMatrix,
    checkpoint_dir: Path,
    scale_pos_weight: float,
    *,
    stop_after_trees: int | None = None,
    checkpoint_identity: str | None = None,
) -> xgb.Booster:
    """Resume the latest checkpoint and train through the fixed maximum or early stopping."""
    identity = checkpoint_identity or config.config_sha256 or "unbound-test-config"
    checkpoint = _latest_checkpoint(checkpoint_dir)
    prior = xgb.Booster(model_file=str(checkpoint)) if checkpoint is not None else None
    state = (
        TrainingCheckpointState.model_validate_json(
            _checkpoint_state_path(checkpoint).read_text(encoding="utf-8")
        )
        if checkpoint is not None
        else TrainingCheckpointState(
            checkpoint_identity=identity,
            completed_trees=0,
            best_score=None,
            stale_rounds=0,
            early_stopped=False,
        )
    )
    if state.checkpoint_identity != identity:
        raise ValueError("checkpoint identity does not match the current config/features")
    if prior is not None and prior.num_boosted_rounds() != state.completed_trees:
        raise ValueError("checkpoint model and early-stop state disagree")
    completed = state.completed_trees
    if prior is not None and (state.early_stopped or completed >= config.training.max_trees):
        return prior
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    booster = prior
    while completed < config.training.max_trees:
        target = min(
            completed + config.training.checkpoint_every_trees,
            config.training.max_trees,
        )
        if stop_after_trees is not None:
            target = min(target, stop_after_trees)
        requested = target - completed
        if requested <= 0:
            break
        evaluations: dict[str, dict[str, list[float]]] = {}
        booster = xgb.train(
            _params(config, scale_pos_weight),
            train_matrix,
            num_boost_round=requested,
            evals=[(tuning_matrix, "tuning")],
            evals_result=evaluations,
            xgb_model=booster,
            verbose_eval=False,
        )
        state = advance_checkpoint_state(
            state,
            evaluations["tuning"][config.training.eval_metric],
            config.training.early_stopping_rounds,
        )
        if booster.num_boosted_rounds() > state.completed_trees:
            booster = booster[: state.completed_trees]
        _save_checkpoint(checkpoint_dir, booster, state)
        completed = state.completed_trees
        if state.early_stopped or (stop_after_trees is not None and completed >= stop_after_trees):
            break
    if booster is None:
        raise RuntimeError("XGBoost produced no booster")
    return booster
