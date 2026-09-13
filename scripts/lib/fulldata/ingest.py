"""Summary: Memory-bounded verification and typed Parquet ingest for IBM AML CSVs.

Key classes:
- DatasetVerification: checksum and configured source-row verification result.
- RejectionCounts: deterministic row-rejection reasons.
- IngestReconciliation: source, parsed, usable, and rejected row accounting.

Key functions:
- verify_dataset: stream the configured checksum and verify DuckDB's row count.
- ingest_dataset: normalize one CSV into typed monthly Parquet partitions and reconciliation.
- dataset_csv: resolve one configured CSV.
- ingested_path: resolve one source's typed Parquet partition directory.
- ingested_scan: resolve the DuckDB glob for one source's monthly partitions.
- ingested_files: enumerate one source's monthly Parquet partitions.
- reconciliation_path: resolve one reconciliation sidecar.
- load_reconciliation: load the persisted source reconciliation record.

Notes:
- Account keys are source-namespaced and remain only in gitignored local artifacts.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

import duckdb
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from lib.fulldata.config import FullDataConfig, FullDataDataset, resolve_repo_path
from lib.study import atomic_write_model

_HASH_CHUNK_BYTES = 1024 * 1024
_IBM_COLUMNS = (
    "Timestamp",
    "From Bank",
    "Account",
    "To Bank",
    "Account.1",
    "Amount Received",
    "Receiving Currency",
    "Amount Paid",
    "Payment Currency",
    "Payment Format",
    "Is Laundering",
)


class RejectionCounts(BaseModel):
    """Mutually exclusive input rejection counts in validation precedence order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    unparsable_timestamp: int = Field(..., ge=0, description="Rows with invalid timestamps.")
    non_positive_amount: int = Field(..., ge=0, description="Rows with invalid/zero amounts.")
    unknown_currency: int = Field(..., ge=0, description="Rows using an unpinned currency.")
    missing_account: int = Field(..., ge=0, description="Rows with an incomplete account key.")

    @property
    def total(self) -> int:
        """Return the total rejected rows."""
        return sum(self.model_dump().values())


class DatasetVerification(BaseModel):
    """Checksum and source-row verification without implied usability accounting."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate: str = Field(..., description="Configured candidate identity.")
    source: str = Field(..., description="Fetch-registry source identity.")
    source_rows: int = Field(..., ge=0, description="CSV data rows verified.")
    input_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$", description="CSV byte hash.")


class IngestReconciliation(BaseModel):
    """PHI-free source-to-usable row reconciliation for one ingest stage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate: str = Field(..., description="Configured candidate identity.")
    source: str = Field(..., description="Fetch-registry source identity.")
    source_rows: int = Field(..., ge=0, description="CSV rows considered by this stage.")
    parsed_rows: int = Field(..., ge=0, description="Rows with a valid timestamp.")
    usable_rows: int = Field(..., ge=0, description="Rows emitted to typed Parquet.")
    rejected: RejectionCounts = Field(..., description="Exclusive rejection counts.")
    input_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$", description="CSV byte hash.")
    limited_rows: int | None = Field(default=None, gt=0, description="Pilot input cap, if any.")

    @model_validator(mode="after")
    def _reconciles(self) -> IngestReconciliation:
        if self.usable_rows + self.rejected.total != self.source_rows:
            raise ValueError("usable plus rejected rows must equal source rows")
        if self.parsed_rows != self.source_rows - self.rejected.unparsable_timestamp:
            raise ValueError("parsed_rows must exclude only unparsable timestamps")
        return self


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_csv_sql(path: Path, row_limit: int | None) -> tuple[str, list[Any]]:
    """Return a duplicate-header-safe DuckDB CSV scan and its bound parameters."""
    placeholders = ", ".join("?" for _ in _IBM_COLUMNS)
    limit = "" if row_limit is None else " LIMIT ?"
    params: list[Any] = [str(path), *_IBM_COLUMNS]
    if row_limit is not None:
        params.append(row_limit)
    return (
        "SELECT row_number() OVER () - 1 AS row_id, * FROM "
        f"read_csv(?, names=[{placeholders}], header=false, skip=1, all_varchar=true){limit}",
        params,
    )


def _usd_rates(config: FullDataConfig) -> tuple[str, ...]:
    path = resolve_repo_path(config.usable_rules.usd_rates_file)
    payload: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    rates = payload["usd_rates"]
    return tuple(sorted(str(key).strip().lower() for key in rates))


def dataset_csv(config: FullDataConfig, dataset: FullDataDataset, repo_root: Path) -> Path:
    """Resolve one configured input path below the caller's repository root."""
    return repo_root / config.paths.data_dir / dataset.file


def ingested_path(config: FullDataConfig, dataset: FullDataDataset, repo_root: Path) -> Path:
    """Resolve the typed Parquet partition directory for one source."""
    return repo_root / config.paths.work_dir / "ingested" / dataset.source


def ingested_scan(config: FullDataConfig, dataset: FullDataDataset, repo_root: Path) -> str:
    """Resolve the DuckDB glob covering one source's monthly Parquet partitions."""
    return str(ingested_path(config, dataset, repo_root) / "month=*" / "part-*.parquet")


def ingested_files(
    config: FullDataConfig, dataset: FullDataDataset, repo_root: Path
) -> tuple[Path, ...]:
    """Return one source's monthly partitions in stable order."""
    return tuple(sorted(ingested_path(config, dataset, repo_root).glob("month=*/part-*.parquet")))


def reconciliation_path(config: FullDataConfig, dataset: FullDataDataset, repo_root: Path) -> Path:
    """Resolve the reconciliation sidecar for one source."""
    return repo_root / config.paths.work_dir / "reconciliation" / f"{dataset.source}.json"


def verify_dataset(
    config: FullDataConfig, dataset: FullDataDataset, repo_root: Path
) -> DatasetVerification:
    """Verify a configured file's checksum and row count without retaining row data."""
    path = dataset_csv(config, dataset, repo_root)
    if not path.is_file():
        raise FileNotFoundError(f"configured full-data file is absent: {dataset.file}")
    observed_hash = _sha256(path)
    if observed_hash != dataset.sha256:
        raise ValueError(f"{dataset.file}: SHA-256 does not match config/fulldata.yaml")
    scan_sql, params = _read_csv_sql(path, None)
    with duckdb.connect() as connection:
        row = connection.execute(f"SELECT count(*) FROM ({scan_sql})", params).fetchone()
    if row is None:
        raise RuntimeError("DuckDB returned no verification count")
    row_count = int(row[0])
    if row_count != dataset.rows_expected:
        raise ValueError(
            f"{dataset.file}: expected {dataset.rows_expected} rows, observed {row_count}"
        )
    return DatasetVerification(
        candidate=dataset.candidate,
        source=dataset.source,
        source_rows=row_count,
        input_sha256=observed_hash,
    )


def _classified_sql(scan_sql: str, currencies: tuple[str, ...]) -> str:
    currency_values = ", ".join("?" for _ in currencies)
    return f"""
        WITH raw AS ({scan_sql}), normalized AS (
          SELECT *,
            try_strptime(trim("Timestamp"), '%Y/%m/%d %H:%M') AS occurred_at,
            try_cast(
              round(try_cast(trim("Amount Paid") AS DECIMAL(38, 10)), 2)
              AS DECIMAL(38, 2)
            ) AS amount,
            lower(trim("Payment Currency")) AS currency_key
          FROM raw
        )
        SELECT *, CASE
          WHEN occurred_at IS NULL THEN 'unparsable_timestamp'
          WHEN amount IS NULL OR amount <= 0 THEN 'non_positive_amount'
          WHEN currency_key NOT IN ({currency_values}) THEN 'unknown_currency'
          WHEN nullif(trim("From Bank"), '') IS NULL OR nullif(trim("Account"), '') IS NULL
            OR nullif(trim("To Bank"), '') IS NULL OR nullif(trim("Account.1"), '') IS NULL
            THEN 'missing_account'
          ELSE 'usable'
        END AS disposition
        FROM normalized
    """


def _sql_text(value: str) -> str:
    """Quote a configuration-controlled SQL string literal."""
    return "'" + value.replace("'", "''") + "'"


def ingest_dataset(
    config: FullDataConfig,
    dataset: FullDataDataset,
    repo_root: Path,
    *,
    row_limit: int | None = None,
) -> IngestReconciliation:
    """Normalize one source into typed Parquet with exclusive rejection accounting."""
    csv_path = dataset_csv(config, dataset, repo_root)
    if not csv_path.is_file():
        raise FileNotFoundError(f"configured full-data file is absent: {dataset.file}")
    observed_hash = _sha256(csv_path)
    if row_limit is None and observed_hash != dataset.sha256:
        raise ValueError(f"{dataset.file}: SHA-256 does not match config/fulldata.yaml")
    scan_sql, scan_params = _read_csv_sql(csv_path, row_limit)
    currencies = _usd_rates(config)
    classified = _classified_sql(scan_sql, currencies)
    params = [*scan_params, *currencies]
    output = ingested_path(config, dataset, repo_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.with_name(f".{dataset.source}.staging")
    previous = output.with_name(f".{dataset.source}.previous")
    if previous.is_dir() and not output.exists():
        previous.replace(output)
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(previous, ignore_errors=True)
    with duckdb.connect() as connection:
        counts = dict(
            connection.execute(
                f"SELECT disposition, count(*) FROM ({classified}) GROUP BY disposition", params
            ).fetchall()
        )
        source_rows = sum(int(value) for value in counts.values())
        copy_sql = f"""
          COPY (
            SELECT row_id,
              {_sql_text(dataset.source)} || ':' || trim("From Bank") || ':' ||
                trim("Account") AS origin_account,
              {_sql_text(dataset.source)} || ':' || trim("To Bank") || ':' ||
                trim("Account.1") AS dest_account,
              occurred_at,
              epoch(occurred_at)::BIGINT AS cohort_key,
              amount,
              currency_key AS payment_currency,
              trim("Payment Format") AS payment_format,
              try_cast(trim("Is Laundering") AS UTINYINT) AS label,
              strftime(occurred_at, '%Y-%m') AS month
            FROM ({classified}) WHERE disposition = 'usable'
            ORDER BY occurred_at, row_id
          ) TO {_sql_text(str(staging))} (
            FORMAT PARQUET,
            COMPRESSION ZSTD,
            ROW_GROUP_SIZE 65536,
            PARTITION_BY (month),
            FILENAME_PATTERN 'part-{{i}}'
          )
        """
        connection.execute(copy_sql, params)
    if output.exists():
        output.replace(previous)
    try:
        staging.replace(output)
    except OSError:
        if previous.exists() and not output.exists():
            previous.replace(output)
        raise
    shutil.rmtree(previous, ignore_errors=True)
    rejected = RejectionCounts(
        unparsable_timestamp=int(counts.get("unparsable_timestamp", 0)),
        non_positive_amount=int(counts.get("non_positive_amount", 0)),
        unknown_currency=int(counts.get("unknown_currency", 0)),
        missing_account=int(counts.get("missing_account", 0)),
    )
    result = IngestReconciliation(
        candidate=dataset.candidate,
        source=dataset.source,
        source_rows=source_rows,
        parsed_rows=source_rows - rejected.unparsable_timestamp,
        usable_rows=int(counts.get("usable", 0)),
        rejected=rejected,
        input_sha256=observed_hash,
        limited_rows=row_limit,
    )
    atomic_write_model(reconciliation_path(config, dataset, repo_root), result)
    return result


def load_reconciliation(
    config: FullDataConfig, dataset: FullDataDataset, repo_root: Path
) -> IngestReconciliation:
    """Load a validated reconciliation sidecar."""
    return IngestReconciliation.model_validate_json(
        reconciliation_path(config, dataset, repo_root).read_text(encoding="utf-8")
    )
