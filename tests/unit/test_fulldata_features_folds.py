"""DuckDB 19-feature parity and whole-timestamp-cohort fold tests."""

from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pytest
from fulldata_fakes import fulldata_config

from fraudlens_ml.scoring import FEATURE_NAMES
from lib.fulldata.features import build_features, feature_path, load_feature_build
from lib.fulldata.folds import build_folds, folded_path, load_fold_manifest
from lib.fulldata.ingest import ingest_dataset
from lib.fulldata.parity import load_parity_result, validate_feature_parity


def test_features_match_live_builder_and_emit_ordered_contract(tmp_path: Path) -> None:
    config = fulldata_config(tmp_path)
    dataset = config.datasets[0]
    ingest_dataset(config, dataset, tmp_path)
    build = build_features(config, dataset, tmp_path)
    assert build.row_count == 1996
    assert build.feature_names == FEATURE_NAMES
    assert load_feature_build(config, dataset, tmp_path) == build
    parity = validate_feature_parity(config, dataset, tmp_path, sample_rows=32)
    assert parity.passed is True
    assert parity.max_absolute_error <= 1e-9
    assert load_parity_result(config, dataset, tmp_path) == parity
    with duckdb.connect() as connection:
        columns = connection.execute(
            "DESCRIBE SELECT * FROM read_parquet(?)",
            [str(feature_path(config, dataset, tmp_path))],
        ).fetchall()
    observed = {row[0] for row in columns}
    assert set(FEATURE_NAMES) <= observed


def test_parity_failure_does_not_write_success_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = fulldata_config(tmp_path)
    dataset = config.datasets[0]
    ingest_dataset(config, dataset, tmp_path)
    build_features(config, dataset, tmp_path)

    def shifted(*args: object, **kwargs: object) -> tuple[np.ndarray, np.ndarray]:
        del args, kwargs
        return np.ones((2000, len(FEATURE_NAMES))), np.zeros(2000)

    monkeypatch.setattr("lib.fulldata.parity.build_feature_matrix", shifted)
    with pytest.raises(ValueError, match="parity failed"):
        validate_feature_parity(config, dataset, tmp_path, sample_rows=2)


def test_folds_keep_timestamp_cohorts_whole_and_split_middle_in_half(tmp_path: Path) -> None:
    config = fulldata_config(tmp_path)
    dataset = config.datasets[0]
    ingest_dataset(config, dataset, tmp_path)
    build_features(config, dataset, tmp_path)
    manifest = build_folds(config, dataset, tmp_path)
    assert load_fold_manifest(config, dataset, tmp_path) == manifest
    counts = manifest.counts
    assert sum(getattr(counts, name).rows for name in type(counts).model_fields) == 1996
    assert all(getattr(counts, name).positives > 0 for name in type(counts).model_fields)
    assert manifest.boundary_epochs == tuple(sorted(manifest.boundary_epochs))
    with duckdb.connect() as connection:
        split_cohorts = connection.execute(
            """
            SELECT count(*) FROM (
              SELECT cohort_key FROM read_parquet(?) GROUP BY cohort_key
              HAVING count(DISTINCT fold) > 1
            )
            """,
            [str(folded_path(config, dataset, tmp_path))],
        ).fetchone()[0]
    assert split_cohorts == 0


def test_feature_and_fold_stages_require_predecessors(tmp_path: Path) -> None:
    config = fulldata_config(tmp_path)
    dataset = config.datasets[0]
    with pytest.raises(FileNotFoundError, match="ingest stage"):
        build_features(config, dataset, tmp_path)
    with pytest.raises(FileNotFoundError, match="feature stage"):
        build_folds(config, dataset, tmp_path)
    with pytest.raises(ValueError, match="positive"):
        validate_feature_parity(config, dataset, tmp_path, sample_rows=0)
