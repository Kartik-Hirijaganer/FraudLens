"""Summary: Hash-bound offline feature inputs and reusable frozen-model predictions.

Key classes:
- PredictionBinding: source/model/metadata/feature/cache identity.

Key functions:
- digest: stream an artifact SHA-256.
- account_evaluation: reconstruct the served bundle's original account-whole split.
- load_predictions: verify inputs and reuse or rebuild a bounded prediction cache.

Notes:
- No database or network calls; synthetic identifiers stay under .local only.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pyarrow.parquet as pq
from pydantic import BaseModel, Field

from fraudlens_ml.scoring import FEATURE_NAMES, load_artifact
from fraudlens_ml.scoring.artifacts import LoadedArtifact
from lib.dataset import _CALIBRATION_FRACTION, _HOLDOUT_FRACTION
from lib.fulldata.config import load_fulldata_config
from lib.risk_study.models import Cohort, StudyConfig, StudyRun
from lib.study import atomic_write_model

_HASH_CHUNK = 1024 * 1024


class PredictionBinding(BaseModel):
    """Bind every cached prediction to all numerical inputs, including calibration metadata."""

    feature_sha256: str = Field(..., description="Evaluated feature/label/fold bytes.")
    source_sha256: str = Field(..., description="Pinned public CSV hash.")
    metadata_sha256: str = Field(..., description="Calibration and normalization metadata hash.")
    model_sha256: str = Field(..., description="Frozen booster bytes.")
    cache_sha256: str = Field(default="", description="Prediction-array bytes.")


def digest(path: Path) -> str:
    """Hash large local artifacts without materializing them in memory."""
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            result.update(chunk)
    return result.hexdigest()


def account_evaluation(features: Path, output: Path) -> Path:
    """Reproduce split_chronological's whole-origin-account allocation, including key sorting.

    Grouped accounts sort by their earliest timestamp and canonical bank+U+001F+account key.
    Assignment uses the rows already allocated BEFORE the account, so a boundary account
    stays wholly in its original fold. This split is not a strict transaction-time holdout.
    """
    feature_hash = digest(features)
    marker = output.with_suffix(".sha256")
    if output.exists() and marker.exists() and marker.read_text().strip() == feature_hash:
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect() as connection:
        connection.execute("SET memory_limit='4GB'")
        total = connection.execute(
            "SELECT count(*) FROM read_parquet(?)", [str(features)]
        ).fetchone()
        assert total is not None
        rows = int(total[0])
        train_target = rows - int(rows * _HOLDOUT_FRACTION) - int(rows * _CALIBRATION_FRACTION)
        calibration_end = train_target + int(rows * _CALIBRATION_FRACTION)
        # Only configuration-controlled paths enter literals; bind every input path/value.
        quoted = "'" + str(output).replace("'", "''") + "'"
        connection.execute(
            f"""COPY (
            WITH accounts AS (
              SELECT origin_account, min(cohort_key) AS first_epoch, count(*) AS rows,
                split_part(origin_account, ':', 2) || chr(31) ||
                split_part(origin_account, ':', 3) AS canonical_key
              FROM read_parquet(?) GROUP BY origin_account
            ), ordered AS (
              SELECT *, coalesce(sum(rows) OVER (ORDER BY first_epoch, canonical_key
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS assigned
              FROM accounts
            ) SELECT f.* EXCLUDE (fold), CASE WHEN a.assigned < ? THEN 'calibration'
                ELSE 'holdout' END AS fold
              FROM read_parquet(?) f JOIN ordered a USING (origin_account)
              WHERE a.assigned >= ? ORDER BY f.cohort_key, f.row_id
          ) TO {quoted} (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 65536)""",
            [str(features), calibration_end, str(features), train_target],
        )
    marker.write_text(feature_hash + "\n", encoding="utf-8")
    return output


def _predict(path: Path, artifact: LoadedArtifact, batch_rows: int) -> Cohort:
    arrays: dict[str, list[Any]] = {k: [] for k in Cohort.model_fields}
    artifact.booster.set_param({"nthread": 4})
    for batch in pq.ParquetFile(path).iter_batches(batch_size=batch_rows):
        values = batch.to_pydict()
        matrix = np.column_stack([values[name] for name in FEATURE_NAMES]).astype(np.float32)
        margin = artifact.booster.inplace_predict(matrix, predict_type="margin")
        arrays["probability"].append(artifact.calibration.apply(margin))
        for target, key in (
            ("row_id", "row_id"),
            ("label", "label"),
            ("rules", "rules_score"),
            ("epoch", "cohort_key"),
        ):
            arrays[target].append(np.asarray(values[key]))
        if not set(values["fold"]) <= {"calibration", "holdout"}:
            raise ValueError("evaluation input contains a training or unknown fold")
        arrays["holdout"].append(np.asarray(values["fold"]) == "holdout")
    return Cohort(**{key: np.concatenate(value) for key, value in arrays.items()})


def load_predictions(
    root: Path, config: StudyConfig, run: StudyRun
) -> tuple[Cohort, LoadedArtifact, PredictionBinding, Path]:
    """Reject stale identities; evaluate only the requested frozen bundle and source."""
    work = root / run.work_dir
    features = work / "features.parquet"
    marker = json.loads((work / "features-complete.json").read_text())
    source = load_fulldata_config().dataset(run.candidate)
    if marker["feature_sha256"] != digest(features) or marker["source_sha256"] != source.sha256:
        raise ValueError("feature/source provenance mismatch")
    model_path = root / run.model_dir
    if digest(model_path / "metadata.json") != run.metadata_sha256:
        raise ValueError("frozen model metadata mismatch")
    artifact = load_artifact(model_path)
    cache_root = root / config.output_dir / run.name
    cache_root.mkdir(parents=True, exist_ok=True)
    evaluation = (
        account_evaluation(features, cache_root / "evaluation.parquet")
        if run.split == "account_whole"
        else features
    )
    binding = PredictionBinding(
        feature_sha256=digest(evaluation),
        source_sha256=source.sha256,
        metadata_sha256=run.metadata_sha256,
        model_sha256=digest(model_path / "model.json"),
    )
    cache, sidecar = cache_root / "predictions.npz", cache_root / "predictions.json"
    if cache.exists() and sidecar.exists():
        stored = PredictionBinding.model_validate_json(sidecar.read_text())
        if stored.model_copy(
            update={"cache_sha256": ""}
        ) == binding and stored.cache_sha256 == digest(cache):
            with np.load(cache, allow_pickle=False) as values:
                cohort = Cohort(**dict(values))
            return cohort, artifact, stored, evaluation
    cohort = _predict(evaluation, artifact, config.batch_rows)
    np.savez_compressed(cache, **{key: getattr(cohort, key) for key in Cohort.model_fields})
    binding = binding.model_copy(update={"cache_sha256": digest(cache)})
    atomic_write_model(sidecar, binding)
    return cohort, artifact, binding, evaluation
