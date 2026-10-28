"""Frozen-cache tamper checks, original split equivalence, and canonical pattern replay."""

from __future__ import annotations

import json
from decimal import Decimal

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from risk_study_fakes import study_fixture

from lib.aml_fraud import IBM_AML, split_chronological
from lib.risk_study.inputs import account_evaluation, digest, load_predictions
from lib.risk_study.models import Cohort
from lib.risk_study.patterns import positive_patterns, summarize_patterns


def test_bound_predictions_reuse_and_corrupt_cache_rebuild(sandbox):
    config, run, values = study_fixture(sandbox)
    cohort, artifact, binding, path = load_predictions(sandbox, config, run)
    assert artifact.version_label == "v0-fixture"
    np.testing.assert_array_equal(cohort.label, values["label"])
    assert binding.feature_sha256 == digest(path)
    cache = sandbox / config.output_dir / run.name / "predictions.npz"
    original = cache.read_bytes()
    cached, _, stored, _ = load_predictions(sandbox, config, run)
    np.testing.assert_array_equal(cached.probability, cohort.probability)
    assert stored == binding
    cache.write_bytes(b"corrupt cache")
    rebuilt, _, rebuilt_binding, _ = load_predictions(sandbox, config, run)
    np.testing.assert_array_equal(rebuilt.probability, cohort.probability)
    assert rebuilt_binding == binding and cache.read_bytes() == original


@pytest.mark.parametrize("change", ["features", "source", "metadata"])
def test_input_identity_mismatch_fails_before_comparison(sandbox, change):
    config, run, _ = study_fixture(sandbox)
    if change == "features":
        (sandbox / run.work_dir / "features.parquet").write_bytes(b"changed")
    elif change == "source":
        marker = sandbox / run.work_dir / "features-complete.json"
        values = json.loads(marker.read_text())
        values["source_sha256"] = "0" * 64
        marker.write_text(json.dumps(values))
    else:
        run = run.model_copy(update={"metadata_sha256": "0" * 64})
    with pytest.raises(ValueError, match="mismatch"):
        load_predictions(sandbox, config, run)


def test_original_account_whole_split_matches_canonical_allocator(sandbox):
    # A large boundary account overruns the nominal target; equal first timestamps test key order.
    banks = ["2"] * 2 + ["1"] * 7 + ["10"]
    rows = np.arange(10)
    raw = pd.DataFrame({"From Bank": banks, "Account": "A", "Timestamp": "2022-09-01 00:00"})
    expected = split_chronological(rows[:, None], rows, raw, IBM_AML)
    path, output = sandbox / "features.parquet", sandbox / "evaluation.parquet"
    pq.write_table(
        pa.table(
            {
                "row_id": rows,
                "cohort_key": np.ones(10, dtype=int),
                "origin_account": [f"source:{bank}:A" for bank in banks],
                "fold": ["holdout"] * 10,
            }
        ),
        path,
    )
    account_evaluation(path, output)
    result = pq.read_table(output).to_pydict()
    observed = dict(zip(result["row_id"], result["fold"], strict=True))
    assert {i for i, fold in observed.items() if fold == "calibration"} == set(
        expected.y_calibration
    )
    assert {i for i, fold in observed.items() if fold == "holdout"} == set(expected.y_holdout)
    assert not set(expected.y_train) & set(observed)
    before = output.read_bytes()
    assert account_evaluation(path, output) == output
    assert output.read_bytes() == before


def test_prediction_rejects_training_fold_and_supports_account_split(sandbox):
    config, run, values = study_fixture(sandbox)
    values["fold"][0] = "train"
    path = sandbox / run.work_dir / "features.parquet"
    pq.write_table(pa.table(values), path)
    marker = sandbox / run.work_dir / "features-complete.json"
    source = json.loads(marker.read_text())
    source["feature_sha256"] = digest(path)
    marker.write_text(json.dumps(source))
    with pytest.raises(ValueError, match="training or unknown fold"):
        load_predictions(sandbox, config, run)
    actual, _, _, evaluated = load_predictions(
        sandbox, config, run.model_copy(update={"split": "account_whole"})
    )
    assert len(actual.label) < len(values["label"]) and evaluated != path


def test_canonical_pattern_replay_excludes_same_time_and_checks_scores(sandbox):
    _config, run, _ = study_fixture(sandbox)
    source = sandbox / run.work_dir / "input/ingested/ibm-aml/month=2022-09"
    source.mkdir(parents=True)
    # The same-time outbound at t=60 must not become a prior for target row 2.
    rows = {
        "row_id": [0, 1, 2],
        "origin_account": ["B", "A", "A"],
        "dest_account": ["A", "C", "C"],
        "cohort_key": [0, 60, 60],
        "amount": [Decimal("10000"), Decimal("9500"), Decimal("9500")],
        "payment_currency": ["us dollar"] * 3,
        "payment_format": ["Wire"] * 3,
    }
    pq.write_table(pa.table(rows), source / "part-0.parquet")
    cohort = Cohort(
        row_id=np.array([1, 2]),
        label=np.array([0, 1]),
        probability=np.array([0.1, 0.2]),
        rules=np.array([0, 0.4118]),
        epoch=np.array([60, 60]),
        holdout=np.array([False, True]),
    )
    patterns = positive_patterns(sandbox, run, cohort)
    assert set(patterns[2]) == {"threshold_evasion", "rapid_movement"}
    assert summarize_patterns(np.array([2]), patterns) == {
        "rapid_movement": 1,
        "threshold_evasion": 1,
    }
    assert summarize_patterns(np.array([3]), {3: ()}) == {"no_rule_hit": 1}
    with pytest.raises(ValueError, match="disagree"):
        positive_patterns(sandbox, run, cohort.model_copy(update={"rules": np.zeros(2)}))
    assert positive_patterns(sandbox, run, cohort.model_copy(update={"label": np.zeros(2)})) == {}
    with pytest.raises(ValueError, match="reconcile"):
        positive_patterns(sandbox, run, cohort.model_copy(update={"row_id": np.array([1, 9])}))
