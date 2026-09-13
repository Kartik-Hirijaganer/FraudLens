"""Full-data frozen-config and memory-bounded ingest reconciliation tests."""

from __future__ import annotations

import hashlib
from fractions import Fraction
from pathlib import Path

import duckdb
import pytest
from fulldata_fakes import FIXTURE, fulldata_config
from pydantic import ValidationError

from lib.fulldata.config import FoldFractions, FullDataPaths, load_fulldata_config
from lib.fulldata.ingest import (
    ingest_dataset,
    ingested_files,
    ingested_path,
    ingested_scan,
    load_reconciliation,
    verify_dataset,
)


def test_committed_config_is_frozen_complete_and_pre_registered() -> None:
    config = load_fulldata_config()
    assert config.folds.exact() == (Fraction(3, 5), Fraction(1, 5), Fraction(1, 5))
    assert config.thresholds_from == "calibration"
    assert config.candidates == ("hi-small", "hi-medium", "li-medium")
    assert config.application_candidate == "hi-medium"
    assert config.features.spec_version == 2
    assert sum(dataset.rows_expected for dataset in config.datasets) == 68_228_066
    assert (
        config.config_sha256
        == hashlib.sha256(Path("config/fulldata.yaml").read_bytes()).hexdigest()
    )
    with pytest.raises(KeyError, match="unknown full-data"):
        config.dataset("absent")


@pytest.mark.parametrize(
    "payload",
    [
        {"train": "1/2", "calibration": "1/2", "holdout": "1/2"},
        {"train": "bad", "calibration": "1/5", "holdout": "1/5"},
    ],
)
def test_fraction_contract_fails_closed(payload: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        FoldFractions.model_validate(payload)


@pytest.mark.parametrize("path", ["/tmp/data", ".local", "../.local/data"])
def test_paths_cannot_escape_named_local_scratch(path: str) -> None:
    with pytest.raises(ValidationError, match=r"below \.local"):
        FullDataPaths(data_dir=path, work_dir=".local/work", artifacts_dir=".local/artifacts")


def test_verify_and_ingest_reconcile_planted_rejections(tmp_path: Path) -> None:
    config = fulldata_config(tmp_path)
    dataset = config.datasets[0]
    verified = verify_dataset(config, dataset, tmp_path)
    assert verified.source_rows == 2000
    result = ingest_dataset(config, dataset, tmp_path)
    assert result.source_rows == 2000
    assert result.parsed_rows == 1999
    assert result.usable_rows == 1996
    assert result.rejected.model_dump() == {
        "unparsable_timestamp": 1,
        "non_positive_amount": 1,
        "unknown_currency": 1,
        "missing_account": 1,
    }
    assert load_reconciliation(config, dataset, tmp_path) == result
    with duckdb.connect() as connection:
        prefixes = connection.execute(
            "SELECT min(starts_with(origin_account, 'ibm-aml:')) FROM read_parquet(?)",
            [ingested_scan(config, dataset, tmp_path)],
        ).fetchone()[0]
    assert prefixes is True
    partitions = ingested_files(config, dataset, tmp_path)
    assert partitions
    assert all(path.parent.name.startswith("month=") for path in partitions)
    assert ingested_path(config, dataset, tmp_path).is_dir()
    assert not any(".staging" in path.name for path in tmp_path.rglob("*"))


def test_verify_detects_missing_hash_and_row_drift(tmp_path: Path) -> None:
    config = fulldata_config(tmp_path)
    dataset = config.datasets[0]
    missing = config.model_copy(
        update={"datasets": (dataset.model_copy(update={"file": "missing.csv"}),)}
    )
    with pytest.raises(FileNotFoundError, match="absent"):
        verify_dataset(missing, missing.datasets[0], tmp_path)
    bad_hash = dataset.model_copy(update={"sha256": "0" * 64})
    with pytest.raises(ValueError, match="SHA-256"):
        verify_dataset(config, bad_hash, tmp_path)
    bad_rows = dataset.model_copy(update={"rows_expected": 1999})
    with pytest.raises(ValueError, match="expected 1999"):
        verify_dataset(config, bad_rows, tmp_path)
    assert FIXTURE.stat().st_size > 0
