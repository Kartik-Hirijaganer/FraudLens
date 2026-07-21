"""GFP paired-benchmark orchestrator tests (GFP plan Phase 5): fold-safe batching
(batches never cross a fold boundary), per-scope materialization alignment, Arm A
trained exactly ONCE and reused across scope tables, fingerprint-drift aborts, a
degenerate fold failing BEFORE any training, signed isolation arithmetic (retained
lift null + note when the global lift is not positive), and deterministic
StudyReport assembly."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from lib.dataset import DataSplit
from lib.gfp.benchmark import (
    ArmPredictions,
    ArmTrainer,
    DatasetBenchmarkInputs,
    DatasetBenchmarkResult,
    build_study_report,
    run_dataset_benchmark,
    split_fingerprint,
    xgboost_arm_trainer,
)
from lib.gfp.boundaries import DatasetProvenance, DatasetStudySpec, FoldAssignment
from lib.gfp.config import load_gfp_benchmark_config
from lib.gfp.edges import GfpEdgeSet, build_gfp_edge_set, with_targets
from lib.gfp.fake import FakeGraphPreprocessor
from lib.gfp.materialize import (
    fold_safe_batches,
    materialize_scope_features,
    materialize_stream_features,
)
from lib.gfp.schema import GraphFeatureConfig, GraphFeatureSchema

_CONFIG = load_gfp_benchmark_config()
_FEATURE_CONFIG = GraphFeatureConfig.from_benchmark(_CONFIG)
_SCHEMA = GraphFeatureSchema.from_config(_FEATURE_CONFIG)
_AGENCIES = 3
_BASE_WIDTH = 19
_ROWS = 30


def _frame(n: int = _ROWS, *, positive_every: int = 3) -> pd.DataFrame:
    rows = [
        {
            "Timestamp": f"2022/09/01 00:{i:02d}",
            "From Bank": str(i % 5),
            "Account": f"S{i}",
            "To Bank": str((i + 2) % 5),
            "Account.1": f"D{i}",
            "Amount Paid": "250.00",
            "Payment Currency": "US Dollar",
            "Is Laundering": "1" if i % positive_every == 0 else "0",
        }
        for i in range(n)
    ]
    return pd.DataFrame(rows, dtype=str)


def _edge_set(**kwargs: int) -> GfpEdgeSet:
    return build_gfp_edge_set(_frame(**kwargs), _CONFIG, agency_count=_AGENCIES)


def _base_features(edge_set: GfpEdgeSet, seed: int = 5) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(size=(edge_set.gfp_matrix.shape[0], _BASE_WIDTH))


def _quality_trainer(*, wider_is_better: bool) -> tuple[list[int], ArmTrainer]:
    """A deterministic fake trainer whose ranking quality depends on feature width."""
    widths: list[int] = []

    def trainer(split: DataSplit, seed: int) -> ArmPredictions:
        del seed
        width = split.x_train.shape[1]
        widths.append(width)
        weight = width / (width + 400) if wider_is_better else 20 / (width + 20)

        def probabilities(features: np.ndarray, labels: np.ndarray) -> np.ndarray:
            ranks = np.argsort(np.argsort(features.sum(axis=1), kind="stable"), kind="stable")
            noise = 0.05 + 0.3 * ranks / max(1, features.shape[0] - 1)
            return np.clip(noise + weight * labels, 0.0, 1.0)

        return ArmPredictions(
            calibration_probabilities=probabilities(split.x_calibration, split.y_calibration),
            holdout_probabilities=probabilities(split.x_holdout, split.y_holdout),
        )

    return widths, trainer


def test_fold_safe_batches_never_cross_a_fold_boundary() -> None:
    folds = np.array([0] * 5 + [1] * 3 + [2] * 4, dtype=np.int8)
    assert list(fold_safe_batches(folds, 4)) == [(0, 4), (4, 5), (5, 8), (8, 12)]
    assert list(fold_safe_batches(folds, 100)) == [(0, 5), (5, 8), (8, 12)]
    with pytest.raises(ValueError, match="positive"):
        list(fold_safe_batches(folds, 0))


def test_materialize_stream_validates_the_engine_output() -> None:
    edges = _edge_set()

    class _LyingEngine:
        feature_names = ("gfp_a", "gfp_b")

        def transform_batch(self, edge_batch: np.ndarray) -> np.ndarray:
            return np.zeros((edge_batch.shape[0] + 1, 2))

    class _NanEngine:
        feature_names = ("gfp_a",)

        def transform_batch(self, edge_batch: np.ndarray) -> np.ndarray:
            return np.full((edge_batch.shape[0], 1), np.nan)

    with pytest.raises(ValueError, match="misaligned feature block"):
        materialize_stream_features(_LyingEngine(), edges.gfp_matrix, edges.folds, 8)
    with pytest.raises(ValueError, match="non-finite"):
        materialize_stream_features(_NanEngine(), edges.gfp_matrix, edges.folds, 8)
    with pytest.raises(ValueError, match="align 1:1"):
        materialize_stream_features(
            FakeGraphPreprocessor(_FEATURE_CONFIG), edges.gfp_matrix, edges.folds[:-1], 8
        )


def test_per_tenant_materialization_realigns_to_global_for_a_stateless_engine() -> None:
    edges = _edge_set()
    factory = lambda: FakeGraphPreprocessor(_FEATURE_CONFIG)  # noqa: E731 - tiny test factory
    global_features = materialize_scope_features(
        edges, factory, scope="global", batch_size=8, agency_count=_AGENCIES
    )
    tenant_features = materialize_scope_features(
        edges, factory, scope="per_tenant", batch_size=8, agency_count=_AGENCIES
    )
    # The fake depends only on (edge id, column): identical matrices prove the
    # per-tenant scatter restored every row into its original position.
    assert np.array_equal(global_features, tenant_features)
    assert global_features.shape == (_ROWS, len(_SCHEMA.feature_names))
    with pytest.raises(ValueError, match="unknown scope"):
        materialize_scope_features(
            edges, factory, scope="mixed", batch_size=8, agency_count=_AGENCIES
        )


def _run(
    *, wider_is_better: bool
) -> tuple[list[int], DatasetBenchmarkInputs, DatasetBenchmarkResult]:
    edges = _edge_set()
    widths, trainer = _quality_trainer(wider_is_better=wider_is_better)
    inputs = DatasetBenchmarkInputs(
        dataset_source="ibm-aml",
        edge_set=edges,
        base_features=_base_features(edges),
        agency_count=_AGENCIES,
        collect_curation_signals=True,
    )
    result = run_dataset_benchmark(
        inputs,
        _CONFIG,
        _SCHEMA,
        lambda: FakeGraphPreprocessor(_FEATURE_CONFIG),
        trainer=trainer,
    )
    return widths, inputs, result


def test_benchmark_trains_arm_a_once_and_covers_every_scope() -> None:
    widths, _, result = _run(wider_is_better=True)
    arm_b_width = _BASE_WIDTH + len(_SCHEMA.arm_b_names)
    arm_c_width = _BASE_WIDTH + len(_SCHEMA.feature_names)
    # Exactly five fits: A once (19 features), then B/C per scope.
    assert widths == [_BASE_WIDTH, arm_b_width, arm_c_width, arm_b_width, arm_c_width]
    assert [(m.arm, m.scope) for m in result.metrics] == [
        ("A", "shared"),
        ("B", "global"),
        ("C", "global"),
        ("B", "per_tenant"),
        ("C", "per_tenant"),
    ]
    assert [(d.from_arm, d.to_arm, d.scope) for d in result.deltas] == [
        ("A", "B", "global"),
        ("B", "C", "global"),
        ("A", "C", "global"),
        ("A", "B", "per_tenant"),
        ("B", "C", "per_tenant"),
        ("A", "C", "per_tenant"),
    ]
    assert all(d.dataset_source == "ibm-aml" for d in result.deltas)
    assert result.curation_signals is not None
    # The stateless fake makes both scopes identical: signed deltas are exactly zero
    # and the retained share of a positive global lift is exactly 1.
    comparison = result.comparison
    assert comparison.isolation_delta_b == pytest.approx(0.0)
    assert comparison.isolation_delta_c == pytest.approx(0.0)
    assert comparison.lost_graph_lift == pytest.approx(0.0)
    assert comparison.retained_graph_lift == pytest.approx(1.0)
    a_pr = next(m.pr_auc for m in result.metrics if m.arm == "A")
    c_pr = next(m.pr_auc for m in result.metrics if m.arm == "C" and m.scope == "global")
    assert c_pr > a_pr  # wider-is-better trainer: the graph arms genuinely lift


def test_benchmark_reports_a_null_retained_lift_with_a_note() -> None:
    _, _, result = _run(wider_is_better=False)
    comparison = result.comparison
    assert comparison.retained_graph_lift is None
    assert comparison.retained_lift_note is not None
    assert "not positive" in comparison.retained_lift_note


def test_benchmark_aborts_when_targets_drift_between_fits() -> None:
    edges = _edge_set()
    inputs = DatasetBenchmarkInputs(
        dataset_source="ibm-aml",
        edge_set=edges,
        base_features=_base_features(edges),
        agency_count=_AGENCIES,
    )
    calls: list[int] = []

    def mutating_trainer(split: DataSplit, seed: int) -> ArmPredictions:
        del seed
        calls.append(split.x_train.shape[1])
        edges.labels[0] ^= 1  # corrupt the shared inputs after the first fit
        flat = np.full(split.x_calibration.shape[0], 0.5)
        return ArmPredictions(
            calibration_probabilities=flat,
            holdout_probabilities=np.full(split.x_holdout.shape[0], 0.5),
        )

    with pytest.raises(RuntimeError, match="fingerprint drifted"):
        run_dataset_benchmark(
            inputs,
            _CONFIG,
            _SCHEMA,
            lambda: FakeGraphPreprocessor(_FEATURE_CONFIG),
            trainer=mutating_trainer,
        )
    assert len(calls) == 1  # the drift was caught before the SECOND fit


def test_benchmark_rejects_a_degenerate_fold_before_training() -> None:
    edges = _edge_set(positive_every=29)  # positives land in train only
    widths, trainer = _quality_trainer(wider_is_better=True)
    inputs = DatasetBenchmarkInputs(
        dataset_source="ibm-aml",
        edge_set=edges,
        base_features=_base_features(edges),
        agency_count=_AGENCIES,
    )
    with pytest.raises(ValueError, match="lacks both label classes"):
        run_dataset_benchmark(
            inputs,
            _CONFIG,
            _SCHEMA,
            lambda: FakeGraphPreprocessor(_FEATURE_CONFIG),
            trainer=trainer,
        )
    assert widths == []  # the benchmark never trained on the degenerate fold
    with pytest.raises(ValueError, match="align 1:1"):
        run_dataset_benchmark(
            DatasetBenchmarkInputs(
                dataset_source="ibm-aml",
                edge_set=edges,
                base_features=_base_features(edges)[:-1],
                agency_count=_AGENCIES,
            ),
            _CONFIG,
            _SCHEMA,
            lambda: FakeGraphPreprocessor(_FEATURE_CONFIG),
            trainer=trainer,
        )


def test_split_fingerprint_tracks_targets_folds_and_labels() -> None:
    edges = _edge_set()
    baseline = split_fingerprint(edges)
    assert baseline == split_fingerprint(_edge_set())
    narrowed = with_targets(edges, np.arange(_ROWS) % 2 == 0)
    assert split_fingerprint(narrowed) != baseline
    relabeled = _edge_set()
    relabeled.labels[0] ^= 1
    assert split_fingerprint(relabeled) != baseline


def test_xgboost_arm_trainer_recovers_calibrated_probabilities() -> None:
    rng = np.random.default_rng(1729)
    features = rng.normal(size=(120, 4))
    labels = (features.sum(axis=1) + rng.normal(scale=0.5, size=120) > 0).astype(np.int64)
    split = DataSplit(
        x_train=features[:80],
        y_train=labels[:80],
        x_calibration=features[80:100],
        y_calibration=labels[80:100],
        x_holdout=features[100:],
        y_holdout=labels[100:],
    )
    first = xgboost_arm_trainer(split, 1729)
    second = xgboost_arm_trainer(split, 1729)
    assert first.holdout_probabilities.shape == (20,)
    assert first.calibration_probabilities.shape == (20,)
    assert np.all((first.holdout_probabilities >= 0) & (first.holdout_probabilities <= 1))
    assert np.array_equal(first.holdout_probabilities, second.holdout_probabilities)


def _provenance(source: str = "ibm-aml") -> DatasetProvenance:
    return DatasetProvenance(
        spec=DatasetStudySpec(source=source, variant="HI-Small_Trans.csv", graph_context="full"),
        file_sha256="b" * 64,
        source_row_count=_ROWS,
        servable_row_count=_ROWS,
        context_edge_count=_ROWS,
        target_count=_ROWS,
        illicit_ratio=0.33,
        fold_assignment=FoldAssignment(
            fractions=("3/5", "1/5", "1/5"),
            boundary_epochs_s=(1, 2),
            fold_sizes=(18, 6, 6),
            fold_positive_counts=(6, 2, 2),
        ),
        fold_target_counts=(18, 6, 6),
        fold_target_positive_counts=(6, 2, 2),
    )


def test_build_study_report_is_deterministic_and_never_serving_eligible() -> None:
    _, _, result = _run(wider_is_better=True)
    kwargs = {
        "config": _CONFIG,
        "config_sha256": "c" * 64,
        "engine_name": "fake",
        "engine_version": "builtin",
        "schema": _SCHEMA,
        "base_feature_count": _BASE_WIDTH,
        "datasets": (_provenance(),),
        "results": (result,),
    }
    report = build_study_report(**kwargs)
    assert report.run_id == build_study_report(**kwargs).run_id
    assert report.run_id.startswith("gfp-")
    assert report.serving_eligible is False
    assert report.arm_feature_counts == {
        "A": _BASE_WIDTH,
        "B": _BASE_WIDTH + len(_SCHEMA.arm_b_names),
        "C": _BASE_WIDTH + len(_SCHEMA.feature_names),
    }
    assert report.graph_feature_names == _SCHEMA.feature_names
    assert len(report.metrics) == 5
    assert len(report.deltas) == 6
    assert report.notes  # the standing disclosures are always committed
    other = build_study_report(**{**kwargs, "config_sha256": "d" * 64})
    assert other.run_id != report.run_id
    with pytest.raises(ValueError, match="exactly one benchmark result"):
        build_study_report(**{**kwargs, "datasets": ()})
