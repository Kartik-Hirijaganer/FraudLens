"""Summary: Deterministic temporary protocol builders for full-data pipeline tests.

Key classes:
- (none)

Key functions:
- fulldata_config: copy the committed IBM fixture and return a bounded test protocol.
- prepare_fulldata: execute ingest, features, parity, and folds for a temporary repository.

Notes:
- Test artifacts stay below pytest's temporary directory and never use application data.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from lib.fulldata.config import FullDataConfig, load_fulldata_config
from lib.fulldata.features import build_features
from lib.fulldata.folds import build_folds
from lib.fulldata.ingest import ingest_dataset
from lib.fulldata.parity import validate_feature_parity

FIXTURE = Path(__file__).with_name("ibm_sample.csv")


def fulldata_config(repo_root: Path, *, max_trees: int = 16) -> FullDataConfig:
    """Copy the committed 2,000-row fixture and return a fast, hash-bound test config."""
    config = load_fulldata_config()
    data_dir = repo_root / ".local" / "aml_data"
    data_dir.mkdir(parents=True)
    target = data_dir / FIXTURE.name
    shutil.copyfile(FIXTURE, target)
    dataset = config.datasets[0].model_copy(
        update={
            "file": FIXTURE.name,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "rows_expected": 2000,
        }
    )
    training = config.training.model_copy(
        update={
            "max_trees": max_trees,
            "early_stopping_rounds": max_trees + 1,
            "checkpoint_every_trees": 4,
            "baseline_sample_rows": 1000,
            "parquet_batch_rows": 128,
            "shap_background_rows": 8,
            "threads": 1,
        }
    )
    paths = config.paths.model_copy(
        update={
            "data_dir": ".local/aml_data",
            "work_dir": ".local/fulldata",
            "artifacts_dir": ".local/fulldata/artifacts",
        }
    )
    return config.model_copy(
        update={
            "datasets": (dataset,),
            "candidates": ("hi-small",),
            "application_candidate": "hi-small",
            "training": training,
            "paths": paths,
            "pilot": config.pilot.model_copy(update={"row_targets": (1000, 2000)}),
            "config_sha256": "f" * 64,
        }
    )


def prepare_fulldata(config: FullDataConfig, repo_root: Path) -> None:
    """Run every deterministic pre-training stage for the configured fixture source."""
    dataset = config.datasets[0]
    ingest_dataset(config, dataset, repo_root)
    build_features(config, dataset, repo_root)
    validate_feature_parity(config, dataset, repo_root, sample_rows=24)
    build_folds(config, dataset, repo_root)
