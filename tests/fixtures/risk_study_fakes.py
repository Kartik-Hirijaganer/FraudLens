"""Summary: Small synthetic feature/provenance fixtures for the offline policy study.

Key classes:
- (none)

Key functions:
- study_fixture: bind a tiny local feature file to the real committed fixture booster.

Notes:
- No training, network, credentials or application-database access.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from fraudlens_ml.scoring import FEATURE_NAMES
from lib.fulldata.config import load_fulldata_config
from lib.risk_study.inputs import digest
from lib.risk_study.models import load_config

ROOT = Path(__file__).resolve().parents[2]


def study_fixture(sandbox: Path):
    """Supply twelve rows in disjoint folds and a hash-verifiable existing model bundle."""
    work = sandbox / ".local/input"
    work.mkdir(parents=True)
    model = sandbox / "data/models/fixture"
    shutil.copytree(ROOT / "data/models/v0-fixture", model)
    values = {name: np.linspace(0.1, 0.9, 12) for name in FEATURE_NAMES}
    values.update(
        {
            "row_id": np.arange(12),
            "label": np.tile([0, 0, 1], 4),
            "rules_score": np.zeros(12),
            "cohort_key": np.arange(12) * 86400,
            "origin_account": [f"source:1:{i}" for i in range(12)],
            "dest_account": [f"source:2:{i}" for i in range(12)],
            "fold": ["calibration"] * 6 + ["holdout"] * 6,
        }
    )
    pq.write_table(pa.table(values), work / "features.parquet")
    source = load_fulldata_config().dataset("hi-small")
    (work / "features-complete.json").write_text(
        json.dumps(
            {"feature_sha256": digest(work / "features.parquet"), "source_sha256": source.sha256}
        )
    )
    config = load_config(ROOT / "config/risk-ranking.yaml")
    run = config.runs[0].model_copy(
        update={
            "name": "fixture",
            "candidate": "hi-small",
            "work_dir": ".local/input",
            "model_dir": "data/models/fixture",
            "split": "temporal",
            "metadata_sha256": digest(model / "metadata.json"),
        }
    )
    config = config.model_copy(update={"runs": [run], "output_dir": ".local/results"})
    return config, run, values
