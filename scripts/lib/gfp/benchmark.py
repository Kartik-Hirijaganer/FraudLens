"""Summary: The pure paired A/B/C benchmark orchestrator for the offline GFP
tenant-isolation study (GFP plan Phase 5; serving boundary: ADR-017). For one dataset
it assembles the three feature arms — A = the 19 served features (trained ONCE, scope
"shared"), B = A + fan/degree/vertex statistics, C = B + multi-hop patterns — per
graph scope (global, per-tenant), trains each with the PUBLIC `train_candidate`
pipeline (identical folds, labels, seed, calibration; XGBoost hyperparameters are
never duplicated here), recovers calibrated probabilities from the returned booster +
Platt calibration, and evaluates the frozen metric contract with paired bootstrap
intervals. Target/fold/label fingerprints are validated immediately before EVERY fit —
any discrepancy aborts the run. Nothing here touches a database, the model registry,
activation, or artifact directories; `StudyReport` is always `servingEligible=false`.

Key classes:
- ArmPredictions: one arm's calibrated calibration + holdout probabilities.
- DatasetBenchmarkInputs: one dataset's edge set + sorted Arm-A features.
- DatasetBenchmarkResult: metrics, deltas, isolation comparison, curation signals.

Key functions:
- xgboost_arm_trainer: the default trainer — public train_candidate + calibrated probs.
- split_fingerprint: fingerprint the target ids/folds/labels an arm trains on.
- run_dataset_benchmark: run the full paired A/B/C x scope benchmark for one dataset.
- library_versions: pinned library versions observed at run time (report provenance).
- build_study_report: assemble the validated StudyReport for one full run.

Notes:
- Scopes run SEQUENTIALLY and each scope's feature matrix is dropped before the next
  is materialized (the snapml adapter's documented memory budget); curation signals
  are reduced from the two scope matrices for full-context datasets only.
- The Arm-A replicate PR-AUCs are computed once and reused by every scope's paired
  deltas, so A -> B / B -> C / A -> C intervals share identical resampled ids.
- `retained_graph_lift` is None (with a required note) whenever the global Arm-C lift
  is not positive — the signed-delta honesty rule.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata

import numpy as np
import xgboost as xgb

from fraudlens_ml.scoring.gates import ModelGates
from lib.dataset import DataSplit
from lib.gfp.boundaries import DatasetProvenance
from lib.gfp.config import GfpBenchmarkConfig
from lib.gfp.curation import CurationSignals, curation_signals
from lib.gfp.edges import GfpEdgeSet
from lib.gfp.folds import FOLD_CALIBRATION, FOLD_HOLDOUT, FOLD_TRAIN
from lib.gfp.materialize import (
    GLOBAL_SCOPE,
    PER_TENANT_SCOPE,
    EngineFactory,
    materialize_scope_features,
)
from lib.gfp.metrics import (
    BootstrapPlan,
    arm_metrics,
    bootstrap_plan,
    delta_interval,
    replicate_pr_aucs,
)
from lib.gfp.report import ArmDelta, ArmMetrics, ScopeComparison, StudyReport
from lib.gfp.schema import GraphFeatureSchema
from train_model import train_candidate

_FOLD_NAMES = {FOLD_TRAIN: "train", FOLD_CALIBRATION: "calibration", FOLD_HOLDOUT: "holdout"}
_SCOPES: tuple[str, ...] = (GLOBAL_SCOPE, PER_TENANT_SCOPE)
_GRAPH_ARMS: tuple[str, ...] = ("B", "C")
# Library versions recorded as report provenance (the engine's own version is added by
# the caller, which knows which engine actually ran).
_PROVENANCE_LIBRARIES: tuple[str, ...] = ("numpy", "pandas", "scikit-learn", "xgboost")
_RUN_ID_DIGEST_LENGTH = 16

# The plan's standing disclosures, committed on every report (plan "Non-goals & risks").
STUDY_DISCLOSURES: tuple[str, ...] = (
    "GFP transforms are batch-causal (128-edge batches), not strict row-at-a-time serving "
    "parity; the existing anti-skew evidence covers only Arm A's 19 served features.",
    "Node-induced medium samples omit paths crossing discarded nodes, biasing graph-pattern "
    "counts downward on those datasets.",
    "Paired bootstrap intervals use 200 deterministic stratified replicates over a fixed "
    "<=250,000-row holdout subset per dataset.",
)


@dataclass(frozen=True)
class ArmPredictions:
    """One trained arm's calibrated probabilities on the calibration + holdout folds."""

    calibration_probabilities: np.ndarray
    holdout_probabilities: np.ndarray


# One arm's trainer: (split, seed) -> calibrated fold probabilities. Injectable so the
# orchestrator tests run without XGBoost fits; the default is the public pipeline.
ArmTrainer = Callable[[DataSplit, int], ArmPredictions]


def xgboost_arm_trainer(split: DataSplit, seed: int) -> ArmPredictions:
    """Train via the PUBLIC train_candidate and recover its calibrated probabilities.

    Hyperparameters, the rare-event branch, and Platt calibration all live inside
    `train_candidate` — this function never duplicates them (plan Phase 5). The
    booster's raw margins are mapped through the returned calibration, which was fit
    on the calibration fold only.
    """
    trained = train_candidate(split, ModelGates(), seed=seed)
    calibration_margin = np.asarray(
        trained.booster.predict(xgb.DMatrix(split.x_calibration), output_margin=True)
    )
    holdout_margin = np.asarray(
        trained.booster.predict(xgb.DMatrix(split.x_holdout), output_margin=True)
    )
    return ArmPredictions(
        calibration_probabilities=trained.calibration.apply(calibration_margin),
        holdout_probabilities=trained.calibration.apply(holdout_margin),
    )


@dataclass(frozen=True)
class DatasetBenchmarkInputs:
    """One dataset's benchmark inputs: the edge set + edge-order-sorted Arm-A features."""

    dataset_source: str
    edge_set: GfpEdgeSet
    base_features: np.ndarray  # (n, 19) in EDGE order (frame features via original_row_id)
    agency_count: int
    collect_curation_signals: bool = False


@dataclass(frozen=True)
class DatasetBenchmarkResult:
    """One dataset's benchmark outputs on the frozen report contract."""

    metrics: tuple[ArmMetrics, ...]
    deltas: tuple[ArmDelta, ...]
    comparison: ScopeComparison
    curation_signals: CurationSignals | None


def split_fingerprint(edge_set: GfpEdgeSet) -> str:
    """Fingerprint the exact target ids, fold ids, and labels an arm trains on.

    Recomputed immediately before EVERY fit and compared to the run's frozen value:
    identical inputs across arms/scopes are the experiment's core invariant, and any
    drift (a mutated array, a swapped mask) aborts the run rather than producing an
    unpaired comparison.
    """
    targets = np.flatnonzero(edge_set.is_target)
    digest = hashlib.sha256()
    digest.update(edge_set.original_row_id[targets].tobytes())
    digest.update(edge_set.folds[targets].tobytes())
    digest.update(edge_set.labels[targets].tobytes())
    return digest.hexdigest()


def _verify_alignment(expected: str, edge_set: GfpEdgeSet, arm: str, scope: str) -> None:
    """Abort the run when an arm would train on drifted targets/folds/labels."""
    observed = split_fingerprint(edge_set)
    if observed != expected:
        raise RuntimeError(
            f"target/fold/label fingerprint drifted before Arm {arm} ({scope}) — "
            "invariant violation; aborting the run"
        )


def _validate_fold_classes(inputs: DatasetBenchmarkInputs) -> None:
    """Every target fold must carry both label classes BEFORE any training (value-free)."""
    edge_set = inputs.edge_set
    targets = edge_set.is_target
    for fold_id, name in _FOLD_NAMES.items():
        mask = targets & (edge_set.folds == fold_id)
        positives = int(edge_set.labels[mask].sum())
        if positives == 0 or positives == int(mask.sum()):
            raise ValueError(
                f"dataset '{inputs.dataset_source}': target fold '{name}' lacks both label "
                "classes — the benchmark never trains on a degenerate fold"
            )


def _arm_split(features: np.ndarray, edge_set: GfpEdgeSet) -> DataSplit:
    """Assemble one arm's DataSplit from the frozen target mask + fold ids."""
    targets = edge_set.is_target
    train = targets & (edge_set.folds == FOLD_TRAIN)
    calibration = targets & (edge_set.folds == FOLD_CALIBRATION)
    holdout = targets & (edge_set.folds == FOLD_HOLDOUT)
    labels = edge_set.labels.astype(np.int64)
    return DataSplit(
        x_train=features[train],
        y_train=labels[train],
        x_calibration=features[calibration],
        y_calibration=labels[calibration],
        x_holdout=features[holdout],
        y_holdout=labels[holdout],
    )


def _evaluate_arm(  # noqa: PLR0913 - one evaluation binds arm identity + folds + the plan
    inputs: DatasetBenchmarkInputs,
    features: np.ndarray,
    arm: str,
    scope: str,
    expected_fingerprint: str,
    trainer: ArmTrainer,
    seed: int,
    gates: ModelGates,
    plan: BootstrapPlan,
) -> tuple[ArmMetrics, np.ndarray]:
    """Fingerprint-check, train, and evaluate one arm; return metrics + replicate AUCs."""
    _verify_alignment(expected_fingerprint, inputs.edge_set, arm, scope)
    split = _arm_split(features, inputs.edge_set)
    predictions = trainer(split, seed)
    metrics = arm_metrics(
        dataset_source=inputs.dataset_source,
        arm=arm,  # type: ignore[arg-type]
        scope=scope,  # type: ignore[arg-type]
        holdout_labels=split.y_holdout,
        holdout_probabilities=predictions.holdout_probabilities,
        calibration_labels=split.y_calibration,
        calibration_probabilities=predictions.calibration_probabilities,
        gates=gates,
    )
    replicates = replicate_pr_aucs(plan, split.y_holdout, predictions.holdout_probabilities)
    return metrics, replicates


def _scope_comparison(dataset_source: str, pr_auc: dict[tuple[str, str], float]) -> ScopeComparison:
    """Assemble the signed isolation arithmetic from the five point PR-AUCs."""
    arm_a = pr_auc[("A", "shared")]
    c_global = pr_auc[("C", GLOBAL_SCOPE)]
    c_tenant = pr_auc[("C", PER_TENANT_SCOPE)]
    global_lift = c_global - arm_a
    retained: float | None = None
    note: str | None = None
    if global_lift > 0:
        retained = (c_tenant - arm_a) / global_lift
    else:
        note = (
            "global Arm-C lift over Arm A is not positive, so the retained share of "
            "graph lift is undefined"
        )
    return ScopeComparison(
        dataset_source=dataset_source,
        isolation_delta_b=pr_auc[("B", GLOBAL_SCOPE)] - pr_auc[("B", PER_TENANT_SCOPE)],
        isolation_delta_c=c_global - c_tenant,
        lost_graph_lift=(c_global - arm_a) - (c_tenant - arm_a),
        retained_graph_lift=retained,
        retained_lift_note=note,
    )


def run_dataset_benchmark(
    inputs: DatasetBenchmarkInputs,
    config: GfpBenchmarkConfig,
    schema: GraphFeatureSchema,
    engine_factory: EngineFactory,
    *,
    trainer: ArmTrainer = xgboost_arm_trainer,
) -> DatasetBenchmarkResult:
    """Run the paired A/B/C x scope benchmark for one dataset (pure, no IO).

    Arm A trains ONCE (scope "shared") and is reused against both scope tables; arms
    B and C train per scope on the base features + the scope's engineered columns.
    Every fit is preceded by a fingerprint check; scopes run sequentially and each
    scope matrix is released before the next materializes.
    """
    edge_set = inputs.edge_set
    if inputs.base_features.shape[0] != edge_set.gfp_matrix.shape[0]:
        raise ValueError("base features must align 1:1 with the edge set")
    _validate_fold_classes(inputs)
    expected = split_fingerprint(edge_set)
    base = inputs.base_features.astype(np.float32)
    holdout_labels = edge_set.labels[edge_set.is_target & (edge_set.folds == FOLD_HOLDOUT)].astype(
        np.int64
    )
    plan = bootstrap_plan(holdout_labels, seed=config.seed)
    gates = ModelGates()

    metrics_a, replicates_a = _evaluate_arm(
        inputs, base, "A", "shared", expected, trainer, config.seed, gates, plan
    )
    all_metrics: list[ArmMetrics] = [metrics_a]
    deltas: list[ArmDelta] = []
    point_pr_auc: dict[tuple[str, str], float] = {("A", "shared"): metrics_a.pr_auc}
    arm_b_columns = list(schema.column_indices(schema.arm_b_names))
    global_features: np.ndarray | None = None
    signals: CurationSignals | None = None

    for scope in _SCOPES:
        scope_features = materialize_scope_features(
            edge_set,
            engine_factory,
            scope=scope,
            batch_size=config.batch_size,
            agency_count=inputs.agency_count,
        )
        replicate_vectors: dict[str, np.ndarray] = {"A": replicates_a}
        for arm in _GRAPH_ARMS:
            engineered = scope_features[:, arm_b_columns] if arm == "B" else scope_features
            features = np.concatenate([base, engineered.astype(np.float32)], axis=1)
            metrics, replicates = _evaluate_arm(
                inputs, features, arm, scope, expected, trainer, config.seed, gates, plan
            )
            all_metrics.append(metrics)
            point_pr_auc[(arm, scope)] = metrics.pr_auc
            replicate_vectors[arm] = replicates
        for from_arm, to_arm in (("A", "B"), ("B", "C"), ("A", "C")):
            deltas.append(
                ArmDelta(
                    dataset_source=inputs.dataset_source,
                    from_arm=from_arm,  # type: ignore[arg-type]
                    to_arm=to_arm,  # type: ignore[arg-type]
                    scope=scope,  # type: ignore[arg-type]
                    pr_auc_delta=point_pr_auc[(to_arm, scope)]
                    - (
                        point_pr_auc[("A", "shared")]
                        if from_arm == "A"
                        else point_pr_auc[(from_arm, scope)]
                    ),
                    interval=delta_interval(replicate_vectors[from_arm], replicate_vectors[to_arm]),
                )
            )
        if inputs.collect_curation_signals and scope == GLOBAL_SCOPE:
            global_features = scope_features
        elif inputs.collect_curation_signals and scope == PER_TENANT_SCOPE:
            if global_features is None:  # scopes iterate global first, structurally
                raise RuntimeError("global features must be materialized before per-tenant")
            signals = curation_signals(schema, global_features, scope_features)
            global_features = None
        del scope_features  # sequential-scope memory discipline (adapter budget note)

    comparison = _scope_comparison(inputs.dataset_source, point_pr_auc)
    return DatasetBenchmarkResult(
        metrics=tuple(all_metrics),
        deltas=tuple(deltas),
        comparison=comparison,
        curation_signals=signals,
    )


def library_versions() -> dict[str, str]:
    """Pinned library versions observed at run time (PHI-free report provenance)."""
    return {name: metadata.version(name) for name in _PROVENANCE_LIBRARIES}


def _run_id(
    config_sha256: str,
    engine_name: str,
    engine_version: str,
    seed: int,
    datasets: tuple[DatasetProvenance, ...],
) -> str:
    """Deterministic run id: a digest of the frozen protocol + engine + dataset bytes."""
    payload = json.dumps(
        {
            "configSha256": config_sha256,
            "engine": {"name": engine_name, "version": engine_version},
            "seed": seed,
            "datasets": [
                {"source": item.spec.source, "sha256": item.file_sha256} for item in datasets
            ],
        },
        sort_keys=True,
    )
    return f"gfp-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:_RUN_ID_DIGEST_LENGTH]}"


def build_study_report(  # noqa: PLR0913 - the report binds protocol + engine + all results
    *,
    config: GfpBenchmarkConfig,
    config_sha256: str,
    engine_name: str,
    engine_version: str,
    schema: GraphFeatureSchema,
    base_feature_count: int,
    datasets: tuple[DatasetProvenance, ...],
    results: tuple[DatasetBenchmarkResult, ...],
) -> StudyReport:
    """Assemble the complete, validated StudyReport for one benchmark run."""
    if len(datasets) != len(results):
        raise ValueError("every dataset needs exactly one benchmark result")
    return StudyReport(
        run_id=_run_id(config_sha256, engine_name, engine_version, config.seed, datasets),
        engine_name=engine_name,  # type: ignore[arg-type]
        engine_version=engine_version,
        library_versions=library_versions(),
        config_sha256=config_sha256,
        seed=config.seed,
        datasets=datasets,
        arm_feature_counts={
            "A": base_feature_count,
            "B": base_feature_count + len(schema.arm_b_names),
            "C": base_feature_count + len(schema.feature_names),
        },
        graph_feature_names=schema.feature_names,
        metrics=tuple(metric for result in results for metric in result.metrics),
        deltas=tuple(delta for result in results for delta in result.deltas),
        comparisons=tuple(result.comparison for result in results),
        serving_eligible=False,
        notes=STUDY_DISCLOSURES,
    )
