"""Summary: Fetch a single real AML dataset file from Kaggle into the gitignored local data
dir (real-AML training plan Phase 2; GFP study plan Phase 2 adds the Medium variants). The
IBM AML-Data (AMLworld) bundle on Kaggle is ~8 GB because it packs six datasets, so this NEVER
pulls the whole zip — it downloads exactly one registry-named variant per call (`ibm-aml` →
`HI-Small_Trans.csv`, `ibm-aml-hi-medium` / `ibm-aml-li-medium` → the Medium files) via
`kaggle datasets download -d <slug> -f <variant>`, then verifies the file and returns a frozen,
PHI-free `DatasetPaths` recording the file name, its SHA-256, row counts, the illicit-label
ratio, and the raw timestamp range (provenance the training + GFP benchmark manifests reuse).
Credentials come ONLY from the `KAGGLE_API_TOKEN` env var (Kaggle's new API-token auth,
injected at runtime via `infisical run --env=prod --path=/ml`); the token is never logged and
never written. The raw CSV is download/cache-only and is never committed or served. Refuses to
run in `environment == "prod"` (FraudLens governance: no real-data ops in prod), even though the
download itself is read-only.

Key classes:
- DatasetSpec: the Kaggle download coordinates + license for one real AML dataset (registry).
- DatasetFile: a fetched file's SHA-256, row/illicit counts, and timestamp range (PHI-free).
- DatasetPaths: the verified on-disk location + per-file provenance of a fetched dataset.

Key functions:
- dataset_spec: resolve a `--source` id to its DatasetSpec (raises on an unknown source).
- download: fetch the single variant file (skip if present unless forced), then verify.
- main: CLI entry — fetch a dataset into settings.aml_data_dir (dev/demo only, prod-refused).

Notes:
- The GFP benchmark never auto-downloads: it fails fast on absent files and points at
  `make fetch-gfp-data`, which invokes this CLI one file at a time.
- Fetching only ever WRITES under the gitignored data dir; `_verify_present` is import-safe for
  the trainer / benchmark to fail fast when files are absent, without ever auto-downloading.
- Provenance is PHI-free by construction: names, hashes, counts, a ratio, and raw timestamp
  strings only — never account tokens or row content. A configured provenance column missing
  from the header fails loud (never silently skipped).
- Row count is derived with csv.reader so quoted embedded newlines never miscount; the count
  excludes the header row.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import subprocess
import zipfile
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from fraudlens_backend.settings import AppSettings, get_settings

REPO_ROOT = Path(__file__).resolve().parents[1]

# Canonical --source ids (also the registry keys). Named so no id is a bare inline literal.
IBM_AML = "ibm-aml"
IBM_AML_HI_MEDIUM = "ibm-aml-hi-medium"
IBM_AML_LI_MEDIUM = "ibm-aml-li-medium"

_KAGGLE_TOKEN_ENV = "KAGGLE_API_TOKEN"
_SHA256_CHUNK_BYTES = 1 << 20  # 1 MiB streaming read for large (multi-GB) CSVs.

# Shared IBM AML-Data (AMLworld) coordinates + provenance columns for every variant entry.
_IBM_AML_SLUG = "ealtman2019/ibm-transactions-for-anti-money-laundering-aml"
_IBM_AML_LICENSE = "CDLA-Sharing-1.0"
_IBM_AML_LABEL_COLUMN = "Is Laundering"
_IBM_AML_TIMESTAMP_COLUMN = "Timestamp"
_ILLICIT_LABEL = "1"  # AMLworld labels are 0/1 strings in the CSV.


class DatasetSpec(BaseModel):
    """The Kaggle download coordinates + license for one real AML dataset (a registry entry)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(..., description="Canonical --source id, e.g. 'ibm-aml'.")
    slug: str = Field(..., description="Kaggle dataset slug '<owner>/<dataset>'.")
    variant: str = Field(..., description="The single file to download (never the ~8 GB bundle).")
    license: str = Field(..., description="Dataset license, recorded in the Phase-4 manifest.")
    label_column: str | None = Field(
        default=None,
        description="CSV column holding the 0/1 illicit label; enables ratio provenance.",
    )
    timestamp_column: str | None = Field(
        default=None,
        description="CSV column holding the transaction timestamp; enables time-range provenance.",
    )


class DatasetFile(BaseModel):
    """A fetched dataset file with its integrity fingerprint and scan provenance (PHI-free)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., description="File name within the dataset directory.")
    sha256: str = Field(..., description="SHA-256 of the file bytes (integrity + provenance).")
    row_count: int = Field(..., ge=0, description="Data rows (excludes the CSV header row).")
    illicit_row_count: int | None = Field(
        default=None, ge=0, description="Rows whose label column equals '1' (None when unlabeled)."
    )
    illicit_ratio: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="illicit_row_count / row_count (None when unlabeled or the file has no rows).",
    )
    first_timestamp: str | None = Field(
        default=None, description="Earliest raw timestamp value in the file (PHI-free string)."
    )
    last_timestamp: str | None = Field(
        default=None, description="Latest raw timestamp value in the file (PHI-free string)."
    )


class _CsvScanStats(BaseModel):
    """Single-pass CSV scan results feeding the PHI-free DatasetFile provenance fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    row_count: int = Field(..., ge=0, description="Data rows (excludes the CSV header row).")
    illicit_row_count: int | None = Field(
        default=None, ge=0, description="Rows whose label column equals '1' (None when unlabeled)."
    )
    first_timestamp: str | None = Field(
        default=None, description="Lexicographic minimum raw timestamp value seen."
    )
    last_timestamp: str | None = Field(
        default=None, description="Lexicographic maximum raw timestamp value seen."
    )


class DatasetPaths(BaseModel):
    """The verified on-disk location + per-file provenance of a fetched dataset (PHI-free)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(..., description="Canonical --source id of the fetched dataset.")
    directory: str = Field(..., description="Directory holding the downloaded file(s).")
    files: list[DatasetFile] = Field(..., description="Per-file name + sha256 + row_count.")


def _ibm_aml_spec(source: str, variant: str) -> DatasetSpec:
    """Build one IBM AML-Data registry entry (shared slug/license/provenance columns)."""
    return DatasetSpec(
        source=source,
        slug=_IBM_AML_SLUG,
        variant=variant,
        license=_IBM_AML_LICENSE,
        label_column=_IBM_AML_LABEL_COLUMN,
        timestamp_column=_IBM_AML_TIMESTAMP_COLUMN,
    )


# The dataset registry: the single source of truth for what each --source downloads. IBM
# AML-Data is the built primary (IEEE-CIS stays on its committed sample, not a Kaggle pull).
# The Medium variants back the offline GFP tenant-isolation benchmark (GFP plan Phase 2);
# each is still fetched ONE file at a time — never the ~8 GB bundle.
_DATASETS: dict[str, DatasetSpec] = {
    IBM_AML: _ibm_aml_spec(IBM_AML, "HI-Small_Trans.csv"),
    IBM_AML_HI_MEDIUM: _ibm_aml_spec(IBM_AML_HI_MEDIUM, "HI-Medium_Trans.csv"),
    IBM_AML_LI_MEDIUM: _ibm_aml_spec(IBM_AML_LI_MEDIUM, "LI-Medium_Trans.csv"),
}


def dataset_spec(source: str) -> DatasetSpec:
    """Resolve a --source id to its DatasetSpec, raising on an unknown source."""
    try:
        return _DATASETS[source]
    except KeyError as exc:
        known = ", ".join(sorted(_DATASETS))
        raise KeyError(f"unknown dataset source '{source}' (known: {known})") from exc


def _data_dir(settings: AppSettings, override: str | None) -> Path:
    """Resolve the AML data dir (override or settings), anchoring a relative path at repo root."""
    raw = Path(override or settings.aml_data_dir)
    return raw if raw.is_absolute() else REPO_ROOT / raw


def _sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of a file, streamed so multi-GB CSVs stay memory-safe."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_SHA256_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _column_index(header: list[str], column: str, path: Path) -> int:
    """Return the index of a configured provenance column, failing loud when it is absent."""
    try:
        return header.index(column)
    except ValueError as exc:
        raise ValueError(
            f"configured provenance column '{column}' not found in the {path.name} header"
        ) from exc


def _scan_csv(path: Path, spec: DatasetSpec) -> _CsvScanStats:
    """Stream the CSV once: row count, optional illicit count, and raw timestamp range.

    IBM AML-Data timestamps are zero-padded 'YYYY/MM/DD HH:MM' strings, so the lexicographic
    min/max tracked here IS the chronological range. Only counts and raw timestamp strings are
    retained — never account tokens or row content (PHI-free provenance).
    """
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header is None:
            return _CsvScanStats(row_count=0)
        label_idx = _column_index(header, spec.label_column, path) if spec.label_column else None
        ts_idx = (
            _column_index(header, spec.timestamp_column, path) if spec.timestamp_column else None
        )
        rows = 0
        illicit = 0 if label_idx is not None else None
        first_ts: str | None = None
        last_ts: str | None = None
        for row in reader:
            rows += 1
            if label_idx is not None and illicit is not None and len(row) > label_idx:
                illicit += row[label_idx].strip() == _ILLICIT_LABEL
            if ts_idx is not None and len(row) > ts_idx:
                value = row[ts_idx]
                first_ts = value if first_ts is None or value < first_ts else first_ts
                last_ts = value if last_ts is None or value > last_ts else last_ts
        return _CsvScanStats(
            row_count=rows,
            illicit_row_count=illicit,
            first_timestamp=first_ts,
            last_timestamp=last_ts,
        )


def _verify_present(spec: DatasetSpec, directory: Path) -> DatasetPaths:
    """Verify the variant file exists + is non-empty; return its PHI-free provenance.

    Import-safe for the trainer / GFP benchmark to fail fast on absent data — it never
    downloads. Raises FileNotFoundError when the expected file is missing or empty.
    """
    target = directory / spec.variant
    if not target.is_file() or target.stat().st_size == 0:
        raise FileNotFoundError(
            f"dataset file not found for source '{spec.source}': expected {spec.variant} under "
            f"{directory} — fetch or place the configured file first (train never auto-downloads)"
        )
    stats = _scan_csv(target, spec)
    ratio = (
        stats.illicit_row_count / stats.row_count
        if stats.illicit_row_count is not None and stats.row_count > 0
        else None
    )
    return DatasetPaths(
        source=spec.source,
        directory=str(directory),
        files=[
            DatasetFile(
                name=spec.variant,
                sha256=_sha256(target),
                row_count=stats.row_count,
                illicit_row_count=stats.illicit_row_count,
                illicit_ratio=ratio,
                first_timestamp=stats.first_timestamp,
                last_timestamp=stats.last_timestamp,
            )
        ],
    )


def _kaggle_download(spec: DatasetSpec, directory: Path, *, force: bool) -> None:
    """Invoke the Kaggle CLI to download the single variant file; never logs the API token."""
    if not os.environ.get(_KAGGLE_TOKEN_ENV):
        raise RuntimeError(
            f"{_KAGGLE_TOKEN_ENV} is not set — inject it with "
            "`infisical run --env=prod --path=/ml -- ...` (never commit the token)"
        )
    command = [
        "kaggle",
        "datasets",
        "download",
        "-d",
        spec.slug,
        "-f",
        spec.variant,
        "-p",
        str(directory),
    ]
    if force:
        command.append("--force")
    # The token lives in the env var the CLI reads; it is never placed on argv or logged.
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"kaggle download failed for '{spec.slug}' (exit {result.returncode}); "
            "check KAGGLE_API_TOKEN access to the dataset"
        )
    _extract_if_zipped(spec, directory)


def _extract_if_zipped(spec: DatasetSpec, directory: Path) -> None:
    """Unzip the single-file `.zip` the Kaggle CLI writes when it wraps the variant."""
    archive = directory / f"{spec.variant}.zip"
    if archive.is_file():
        with zipfile.ZipFile(archive) as bundle:
            bundle.extract(spec.variant, directory)
        archive.unlink()


def download(
    spec: DatasetSpec,
    directory: Path,
    *,
    force: bool = False,
    runner: Callable[[DatasetSpec, Path, bool], None] | None = None,
) -> DatasetPaths:
    """Fetch the single variant file (skipped if already present unless forced), then verify.

    `runner` is the download side effect, injectable so tests exercise the flow without network.
    """
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / spec.variant
    if force or not target.is_file():
        (runner or _run_kaggle)(spec, directory, force)
    return _verify_present(spec, directory)


def _run_kaggle(spec: DatasetSpec, directory: Path, force: bool) -> None:
    """Adapter matching the injectable runner signature to the keyword-only CLI helper."""
    _kaggle_download(spec, directory, force=force)


def _amain(source: str, directory_override: str | None, force: bool) -> int:
    """Resolve the source, fetch its file into the data dir, and print a PHI-free summary."""
    settings = get_settings()
    if settings.environment == "prod":
        print("fetch refused: never fetches real datasets in prod (FraudLens governance)")
        return 1
    spec = dataset_spec(source)
    directory = _data_dir(settings, directory_override)
    try:
        paths = download(spec, directory, force=force)
    except (RuntimeError, ValueError, FileNotFoundError, OSError) as exc:
        print(f"fetch failed: {exc}")
        return 1
    for fetched in paths.files:
        provenance = ""
        if fetched.illicit_ratio is not None:
            provenance += f" illicit={fetched.illicit_row_count} ratio={fetched.illicit_ratio:.6f}"
        if fetched.first_timestamp is not None:
            provenance += f" span={fetched.first_timestamp}..{fetched.last_timestamp}"
        print(
            f"fetch OK ({spec.source}): {fetched.name} rows={fetched.row_count} "
            f"sha256={fetched.sha256[:12]}…{provenance} under {paths.directory}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: fetch one real AML dataset file (dev/demo only; prod-refused)."""
    parser = argparse.ArgumentParser(description="Fetch a single real AML dataset file (Kaggle).")
    parser.add_argument(
        "--source", default=IBM_AML, choices=sorted(_DATASETS), help="Dataset source to fetch."
    )
    parser.add_argument("--dir", default=None, help="Override the data dir (else aml_data_dir).")
    parser.add_argument(
        "--force", action="store_true", help="Re-download even if the file is already present."
    )
    args = parser.parse_args(argv)
    return _amain(args.source, args.dir, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
