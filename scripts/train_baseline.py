"""Summary: The logistic-regression baseline for the §10.5.1 promotion gates (plan §16
Phase 5). The XGBoost candidate must beat a simple, transparent baseline on PR-AUC by a
margin (`beats_baseline` gate) — otherwise the added model complexity is not earning its
keep. This module trains that baseline (a `StandardScaler` + `LogisticRegression` with balanced
class weights) on the SAME split the XGBoost trainer uses, so the comparison is apples-to-apples.
`train_model.py` imports `build_baseline` to compute the baseline PR-AUC inline at evaluation
time (on whatever split `_load_split` produced — synthetic OR the real chronological split); the
`main` CLI is a diagnostic that prints the baseline's holdout PR-AUC so the margin a candidate
must clear is easy to inspect, and takes the same `--source` switch. The baseline is
intentionally NOT registered/served — it exists only as the gate's comparison point.

Key classes:
- BaselineModel: a fitted scaler + logistic-regression baseline classifier.

Key functions:
- build_baseline: fit the scaler + balanced logistic regression on a training fold.
- baseline_probabilities: predict P(fraud) for a feature matrix with the baseline.
- baseline_pr_auc: the baseline's PR-AUC on a holdout (the gate comparison value).
- main: CLI — train the baseline on the synthetic dataset and print its holdout PR-AUC.

Notes:
- Linear-by-construction: the baseline cannot capture the nonlinear AND-interactions the
  synthetic fraud signal hides in, which is exactly why a tree model is expected to beat it.
- It touches no database and consumes only the PHI-free feature matrix — a PR-AUC number out.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from fraudlens_ml.scoring.gates import average_precision
from lib.aml_fraud import IBM_AML, IEEE_CIS
from lib.dataset import DataSplit, split_dataset
from lib.synthetic_fraud import generate_dataset

_LR_MAX_ITER = 1000
_DEFAULT_ROWS = 16000
_DEFAULT_SEED = 1729
_SYNTHETIC = "synthetic"
_SOURCES: tuple[str, ...] = (_SYNTHETIC, IBM_AML, IEEE_CIS)


@dataclass(frozen=True)
class BaselineModel:
    """A fitted scaler + logistic-regression baseline (the gate's comparison model)."""

    scaler: Any
    classifier: Any


def build_baseline(x_train: np.ndarray, y_train: np.ndarray, seed: int) -> BaselineModel:
    """Fit a StandardScaler + balanced logistic regression on a training fold."""
    scaler = StandardScaler().fit(x_train)
    classifier = LogisticRegression(
        max_iter=_LR_MAX_ITER, class_weight="balanced", random_state=seed
    ).fit(scaler.transform(x_train), y_train)
    return BaselineModel(scaler=scaler, classifier=classifier)


def baseline_probabilities(baseline: BaselineModel, features: np.ndarray) -> np.ndarray:
    """Return the baseline's P(fraud) for a feature matrix."""
    scaled = baseline.scaler.transform(features)
    return np.asarray(baseline.classifier.predict_proba(scaled)[:, 1], dtype=np.float64)


def baseline_pr_auc(baseline: BaselineModel, features: np.ndarray, labels: np.ndarray) -> float:
    """Return the baseline's holdout PR-AUC (the value the candidate must beat)."""
    return average_precision(labels, baseline_probabilities(baseline, features))


def _split_for_source(source: str, *, rows: int, seed: int, sample_rows: int | None) -> DataSplit:
    """Return the split the baseline scores on — synthetic here, real via train_model._load_split.

    The real branch defers the import to break the train_model<->train_baseline import cycle, and
    reuses the SAME loader the candidate trains on so the gate comparison stays apples-to-apples.
    """
    if source == _SYNTHETIC:
        return split_dataset(*generate_dataset(rows, seed), seed)
    from fraudlens_backend.settings import get_settings  # noqa: PLC0415 - deferred to break cycle
    from train_model import _load_split  # noqa: PLC0415 - deferred to break import cycle

    split, _manifest = _load_split(
        source, seed=seed, rows=rows, sample_rows=sample_rows, settings=get_settings()
    )
    return split


def main(argv: list[str] | None = None) -> int:
    """CLI: train the LR baseline on the selected source and print its holdout PR-AUC."""
    parser = argparse.ArgumentParser(description="Train + report the LR gate baseline.")
    parser.add_argument(
        "--source", choices=_SOURCES, default=_SYNTHETIC, help="Baseline data source."
    )
    parser.add_argument("--rows", type=int, default=_DEFAULT_ROWS, help="Synthetic dataset size.")
    parser.add_argument("--seed", type=int, default=_DEFAULT_SEED, help="Deterministic seed.")
    parser.add_argument(
        "--sample-rows",
        type=int,
        default=None,
        help="Seeded stratified subsample of a real source.",
    )
    args = parser.parse_args(argv)
    split = _split_for_source(
        args.source, rows=args.rows, seed=args.seed, sample_rows=args.sample_rows
    )
    baseline = build_baseline(split.x_train, split.y_train, args.seed)
    pr_auc = baseline_pr_auc(baseline, split.x_holdout, split.y_holdout)
    print(f"baseline OK ({args.source}): LR holdout PR-AUC = {pr_auc:.4f} (seed={args.seed})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
