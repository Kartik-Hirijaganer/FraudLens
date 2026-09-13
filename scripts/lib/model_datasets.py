"""Summary: Dataset manifests, source loading, fold construction, and version identity.

Key classes:
- DatasetManifest: PHI-free provenance for one training dataset.

Key functions:
- synthetic_manifest: describe a deterministic synthetic dataset.
- load_split: resolve synthetic or verified local source data into evaluation folds.
- version_label: derive a source-bound deterministic model label.

Notes:
- Real-data loading fails fast when local verified files are absent and never downloads.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

import fetch_dataset
from fraudlens_backend.settings import AppSettings
from fraudlens_ml.scoring import current_feature_spec
from lib.aml_fraud import (
    IBM_AML,
    IEEE_CIS,
    build_feature_matrix,
    load_frame,
    sample_frame,
    servable_frame,
    source_columns,
    split_chronological,
)
from lib.dataset import DataSplit, split_dataset
from lib.synthetic_fraud import generate_dataset

SYNTHETIC = "synthetic"
SOURCES: tuple[str, ...] = (SYNTHETIC, IBM_AML, IEEE_CIS)
IEEE_CIS_SPEC = fetch_dataset.DatasetSpec(
    source=IEEE_CIS,
    slug="ieee-fraud-detection",
    variant="train_transaction.csv",
    license="Kaggle Competition Rules (IEEE-CIS Fraud Detection)",
)


class DatasetManifest(BaseModel):
    """The versioned, PHI-free provenance of one training dataset (persisted on TrainingDataset)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(..., description="Dataset source id (e.g. 'synthetic' or 'ibm-aml').")
    row_count: int = Field(..., ge=0, description="Rows actually trained on (after any sampling).")
    label_window: str = Field(..., description="TrainingDataset.label_window tag for this source.")
    snapshot_query: dict[str, Any] = Field(
        ..., description="PHI-free dataset descriptor stored as snapshot_query JSONB."
    )
    content_hash: str = Field(..., description="Deterministic sha-256 of the dataset descriptor.")


def _dataset_hash(seed: int, rows: int) -> str:
    """Return the content hash for the (PHI-free) synthetic dataset manifest."""
    spec = current_feature_spec()
    return hashlib.sha256(
        json.dumps(
            {"source": SYNTHETIC, "features": spec.features, "seed": seed, "rows": rows},
            sort_keys=True,
        ).encode()
    ).hexdigest()


def synthetic_manifest(seed: int, rows: int) -> DatasetManifest:
    """Build the synthetic dataset manifest (unchanged shape, keeps the fixture tests valid)."""
    return DatasetManifest(
        source=SYNTHETIC,
        row_count=rows,
        label_window=SYNTHETIC,
        snapshot_query={"source": SYNTHETIC, "seed": seed, "rows": rows},
        content_hash=_dataset_hash(seed, rows),
    )


def _real_manifest(
    source: str, paths: fetch_dataset.DatasetPaths, *, row_count: int, sample_rows: int | None
) -> DatasetManifest:
    """Build a real manifest: source, license, per-file sha256, schema, version, transform id."""
    spec = current_feature_spec()
    dataset = fetch_dataset.dataset_spec(source) if source == IBM_AML else IEEE_CIS_SPEC
    snapshot_query: dict[str, Any] = {
        "source": source,
        "license": dataset.license,
        "datasetVersion": f"{dataset.slug}:{dataset.variant}",
        "files": [{"name": file.name, "sha256": file.sha256} for file in paths.files],
        "schema": list(source_columns(source)),
        "transformId": f"aml-loader-fs{spec.version}",
    }
    if sample_rows is not None:
        snapshot_query["sampleRows"] = sample_rows
    content_hash = hashlib.sha256(
        json.dumps({**snapshot_query, "rowCount": row_count}, sort_keys=True).encode()
    ).hexdigest()
    return DatasetManifest(
        source=source,
        row_count=row_count,
        label_window=source,
        snapshot_query=snapshot_query,
        content_hash=content_hash,
    )


def load_split(
    source: str, *, seed: int, rows: int, sample_rows: int | None, settings: AppSettings
) -> tuple[DataSplit, DatasetManifest]:
    """Resolve a source to a (DataSplit, manifest): synthetic generates; real verifies + loads."""
    if source == SYNTHETIC:
        split = split_dataset(*generate_dataset(rows, seed), seed)
        return split, synthetic_manifest(seed, rows)
    if source not in SOURCES:
        raise ValueError(f"unknown --source '{source}' (choices: {list(SOURCES)})")
    dataset = fetch_dataset.dataset_spec(source) if source == IBM_AML else IEEE_CIS_SPEC
    # Fail fast if the real data is absent — training NEVER auto-downloads (plan Phase 4).
    paths = fetch_dataset._verify_present(dataset, fetch_dataset._data_dir(settings, None))
    # Only servable rows train: the ingest boundary rejects amounts that round to zero cents,
    # so such rows can never appear in a served database (anti-skew).
    frame = servable_frame(load_frame(paths, source), source)
    if sample_rows is not None:
        frame = sample_frame(frame, source, sample_rows, seed)
    # The online history query caps at investigation_history_max most-recent rows; the offline
    # windows mirror that cap so training features equal what scoring is actually fed.
    features, labels = build_feature_matrix(
        frame, source, history_max=settings.investigation_history_max
    )
    split = split_chronological(features, labels, frame, source)
    return split, _real_manifest(source, paths, row_count=len(frame), sample_rows=sample_rows)


def version_label(manifest: DatasetManifest, seed: int, rows: int) -> str:
    """Return a deterministic candidate label including the source (candidates never collide)."""
    spec = current_feature_spec()
    digest = hashlib.sha256(
        json.dumps(
            {
                "source": manifest.source,
                "contentHash": manifest.content_hash,
                "features": spec.features,
                "seed": seed,
                "rows": rows,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return f"xgb-{manifest.source}-fs{spec.version}-{digest[:10]}"
