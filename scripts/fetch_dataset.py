"""Summary: Fetch a single real AML dataset file from Kaggle into the gitignored local data
dir (real-AML training plan Phase 2). The IBM AML-Data (AMLworld) bundle on Kaggle is ~8 GB
because it packs six datasets, so this NEVER pulls the whole zip — it downloads exactly one
config-named variant (default `HI-Small_Trans.csv`) via `kaggle datasets download -d <slug>
-f <variant>`, then verifies the file and returns a frozen, PHI-free `DatasetPaths` recording
the file name, its SHA-256, and its row count (provenance the Phase-4 training manifest reuses).
Credentials come ONLY from the `KAGGLE_API_TOKEN` env var (Kaggle's new API-token auth,
injected at runtime via `infisical run --env=prod --path=/ml`); the token is never logged and
never written. The raw CSV is download/cache-only and is never committed or served. Refuses to
run in `environment == "prod"` (FraudLens governance: no real-data ops in prod), even though the
download itself is read-only.

Key classes:
- DatasetSpec: the Kaggle download coordinates + license for one real AML dataset (registry).
- DatasetFile: a fetched file with its SHA-256 fingerprint and data-row count (PHI-free).
- DatasetPaths: the verified on-disk location + per-file provenance of a fetched dataset.

Key functions:
- dataset_spec: resolve a `--source` id to its DatasetSpec (raises on an unknown source).
- download: fetch the single variant file (skip if present unless forced), then verify.
- main: CLI entry — fetch a dataset into settings.aml_data_dir (dev/demo only, prod-refused).

Notes:
- The variant is config-named in the registry, so HI-Medium/HI-Large are selectable by editing
  the spec; HI-Small is the default (~5M rows is already large on the fixed 10-feature space).
- Fetching only ever WRITES under the gitignored data dir; `_verify_present` is import-safe for
  the trainer (Phase 4) to fail fast when files are absent, without ever auto-downloading.
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

_KAGGLE_TOKEN_ENV = "KAGGLE_API_TOKEN"
_SHA256_CHUNK_BYTES = 1 << 20  # 1 MiB streaming read for large (multi-GB) CSVs.


class DatasetSpec(BaseModel):
    """The Kaggle download coordinates + license for one real AML dataset (a registry entry)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(..., description="Canonical --source id, e.g. 'ibm-aml'.")
    slug: str = Field(..., description="Kaggle dataset slug '<owner>/<dataset>'.")
    variant: str = Field(..., description="The single file to download (never the ~8 GB bundle).")
    license: str = Field(..., description="Dataset license, recorded in the Phase-4 manifest.")


class DatasetFile(BaseModel):
    """A fetched dataset file with its integrity fingerprint and data-row count (PHI-free)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., description="File name within the dataset directory.")
    sha256: str = Field(..., description="SHA-256 of the file bytes (integrity + provenance).")
    row_count: int = Field(..., ge=0, description="Data rows (excludes the CSV header row).")


class DatasetPaths(BaseModel):
    """The verified on-disk location + per-file provenance of a fetched dataset (PHI-free)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(..., description="Canonical --source id of the fetched dataset.")
    directory: str = Field(..., description="Directory holding the downloaded file(s).")
    files: list[DatasetFile] = Field(..., description="Per-file name + sha256 + row_count.")


# The dataset registry: the single source of truth for what each --source downloads. IBM
# AML-Data is the built primary (IEEE-CIS stays on its committed sample, not a Kaggle pull).
_DATASETS: dict[str, DatasetSpec] = {
    IBM_AML: DatasetSpec(
        source=IBM_AML,
        slug="ealtman2019/ibm-transactions-for-anti-money-laundering-aml",
        variant="HI-Small_Trans.csv",
        license="CDLA-Sharing-1.0",
    ),
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


def _row_count(path: Path) -> int:
    """Return the number of data rows (excluding the header) in a CSV file."""
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        if next(reader, None) is None:
            return 0
        return sum(1 for _ in reader)


def _verify_present(spec: DatasetSpec, directory: Path) -> DatasetPaths:
    """Verify the variant file exists + is non-empty; return its PHI-free provenance.

    Import-safe for the trainer to fail fast on absent data — it never downloads. Raises
    FileNotFoundError when the expected file is missing or empty.
    """
    target = directory / spec.variant
    if not target.is_file() or target.stat().st_size == 0:
        raise FileNotFoundError(
            f"dataset file not found for source '{spec.source}': expected {spec.variant} under "
            f"{directory} — run `make fetch-data` first (train never auto-downloads)"
        )
    return DatasetPaths(
        source=spec.source,
        directory=str(directory),
        files=[
            DatasetFile(name=spec.variant, sha256=_sha256(target), row_count=_row_count(target))
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
    except (RuntimeError, FileNotFoundError, OSError) as exc:
        print(f"fetch failed: {exc}")
        return 1
    for fetched in paths.files:
        print(
            f"fetch OK ({spec.source}): {fetched.name} rows={fetched.row_count} "
            f"sha256={fetched.sha256[:12]}… under {paths.directory}"
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
