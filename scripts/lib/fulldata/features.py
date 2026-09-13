"""Summary: DuckDB SQL feature construction with exact live-scorer window semantics.

Key classes:
- FeatureBuild: source-bound feature artifact provenance.

Key functions:
- build_features: generate all 19 live features from typed IBM Parquet.
- feature_path: resolve one candidate's PHI-free feature matrix.
- feature_build_path: resolve one feature provenance sidecar.
- load_feature_build: load the feature-build provenance sidecar.

Notes:
- SQL CASE expressions are generated from the canonical Python mapping/risk functions.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb
from pydantic import BaseModel, ConfigDict, Field

from fraudlens_ml.scoring import FEATURE_NAMES
from fraudlens_ml.scoring.features import channel_risk, country_risk
from lib.aml_mapping import ibm_currency_country_map, ibm_payment_format_channel_map
from lib.fulldata.config import FullDataConfig, FullDataDataset
from lib.fulldata.feature_windows import materialize_feature_windows
from lib.fulldata.ingest import ingested_files, ingested_scan
from lib.study import atomic_write_model

_HASH_CHUNK_BYTES = 1024 * 1024


class FeatureBuild(BaseModel):
    """Provenance for one materialized source feature matrix."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate: str = Field(..., description="Configured candidate identity.")
    source: str = Field(..., description="Fetch-registry source identity.")
    row_count: int = Field(..., ge=0, description="Feature rows emitted.")
    feature_names: tuple[str, ...] = Field(..., description="Ordered feature contract.")
    parquet_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$", description="Output byte hash.")


def _sql_text(value: str) -> str:
    """Quote a configuration-controlled SQL string literal."""
    return "'" + value.replace("'", "''") + "'"


def _case(column: str, rows: tuple[tuple[str, str | float], ...], fallback: str | float) -> str:
    """Render a deterministic simple CASE expression from canonical Python tables."""
    clauses = " ".join(
        f"WHEN {_sql_text(key)} THEN {_sql_text(value) if isinstance(value, str) else value}"
        for key, value in rows
    )
    rendered_fallback = _sql_text(fallback) if isinstance(fallback, str) else fallback
    return f"CASE lower(trim({column})) {clauses} ELSE {rendered_fallback} END"


def _canonical_expressions() -> tuple[str, str, str, str]:
    """Return country/channel token and risk CASEs derived from canonical Python functions."""
    currency_map = ibm_currency_country_map()
    format_map = ibm_payment_format_channel_map()
    country_rows = tuple(sorted(currency_map.items()))
    channel_rows = tuple(sorted(format_map.items()))
    country_token = _case("payment_currency", country_rows, "ZZ")
    channel_token = _case("payment_format", channel_rows, "other")
    country_scores = tuple((currency, country_risk(country)) for currency, country in country_rows)
    channel_scores = tuple((payment, channel_risk(channel)) for payment, channel in channel_rows)
    return (
        country_token,
        channel_token,
        _case("payment_currency", country_scores, country_risk("ZZ")),
        _case("payment_format", channel_scores, channel_risk("other")),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def feature_path(config: FullDataConfig, dataset: FullDataDataset, repo_root: Path) -> Path:
    """Resolve one source's PHI-free feature Parquet file."""
    return repo_root / config.paths.work_dir / "features" / dataset.source / "part-00000.parquet"


def feature_build_path(config: FullDataConfig, dataset: FullDataDataset, repo_root: Path) -> Path:
    """Resolve one feature-build provenance sidecar."""
    return repo_root / config.paths.work_dir / "features" / dataset.source / "build.json"


def _timeline_sql(input_scan: str) -> str:
    """Build the canonical account-event plus origin/destination marker timeline."""
    country_token, channel_token, _country_score, _channel_score = _canonical_expressions()
    source = f"read_parquet({_sql_text(input_scan)}, hive_partitioning=false)"
    return f"""
      WITH base AS (
        SELECT *,
          {country_token} AS country_token,
          {channel_token} AS channel_token,
          CASE WHEN amount % 100 = 0 THEN 1.0 ELSE 0.0 END AS is_round
        FROM {source}
      )
      , timeline AS (
        SELECT origin_account AS account, cohort_key AS occurred_epoch, row_id,
          1::UTINYINT AS role, amount::DOUBLE AS amount, 0.0 AS inbound,
          is_round, country_token, channel_token FROM base
        UNION ALL
        SELECT dest_account, cohort_key, row_id, 2::UTINYINT, amount::DOUBLE, 0.0,
          is_round, country_token, channel_token FROM base
        UNION ALL
        SELECT origin_account, cohort_key, row_id, 0::UTINYINT, amount::DOUBLE, 0.0,
          is_round, country_token, channel_token FROM base
        UNION ALL
        SELECT dest_account, cohort_key, row_id, 0::UTINYINT, amount::DOUBLE, 1.0,
          is_round, country_token, channel_token FROM base
          WHERE dest_account <> origin_account
      )
      SELECT * FROM timeline
      ORDER BY account, occurred_epoch, CASE WHEN role = 0 THEN 1 ELSE 0 END, row_id, role
    """


def _feature_sql(input_scan: str, origin_path: Path, destination_path: Path) -> str:
    """Join bounded history aggregates and express the ordered 19-feature contract in SQL."""
    country_token, channel_token, country_score, channel_score = _canonical_expressions()
    source = f"read_parquet({_sql_text(input_scan)}, hive_partitioning=false)"
    origins = f"read_parquet({_sql_text(str(origin_path))})"
    destinations = f"read_parquet({_sql_text(str(destination_path))})"
    return f"""
      WITH targets AS (
        SELECT *, {country_token} AS country_token, {channel_token} AS channel_token,
          {country_score}::DOUBLE AS country_score,
          {channel_score}::DOUBLE AS channel_score,
          CASE WHEN amount % 100 = 0 THEN 1.0 ELSE 0.0 END AS is_round
        FROM {source}
      )
      SELECT t.row_id, t.occurred_at, t.cohort_key, t.origin_account, t.dest_account, t.label,
        ln(1.0 + t.amount::DOUBLE) AS amount_log,
        hour(t.occurred_at)::DOUBLE AS hour_of_day,
        isodow(t.occurred_at)::DOUBLE - 1.0 AS day_of_week,
        t.is_round AS is_round_amount,
        t.country_score AS country_risk,
        t.channel_score AS channel_risk,
        o.velocity::DOUBLE AS velocity_24h,
        ln(1.0 + t.amount::DOUBLE + o.amount_sum) AS amount_24h_sum_log,
        o.distinct_countries::DOUBLE AS distinct_countries_24h,
        1.0 AS is_outbound,
        o.inbound_velocity::DOUBLE AS inbound_velocity_24h,
        CASE WHEN o.inbound_velocity = 0 THEN 0.0
          ELSE ln(1.0 + o.inbound_amount) END AS inbound_amount_24h_log,
        ln(1.0 + o.seconds_since_prev) AS seconds_since_prev_txn_log,
        o.distinct_channels::DOUBLE AS distinct_channels_24h,
        (t.is_round + o.round_count) / (1.0 + o.velocity) AS round_amount_share_24h,
        d.inbound_velocity::DOUBLE AS dest_fan_in_24h,
        ln(1.0 + t.amount::DOUBLE + d.inbound_amount) AS dest_inbound_amount_24h_log,
        (d.velocity - d.inbound_velocity)::DOUBLE AS dest_outbound_velocity_24h,
        CASE WHEN d.velocity - d.inbound_velocity = 0 THEN 0.0
          ELSE ln(1.0 + d.amount_sum - d.inbound_amount)
        END AS dest_outbound_amount_24h_log
      FROM targets t
      INNER JOIN {origins} o USING (row_id)
      INNER JOIN {destinations} d USING (row_id)
      ORDER BY t.occurred_at, t.row_id
    """


def build_features(
    config: FullDataConfig, dataset: FullDataDataset, repo_root: Path
) -> FeatureBuild:
    """Build the 19-feature matrix through a bounded account stream and final DuckDB SQL."""
    source = ingested_scan(config, dataset, repo_root)
    if not ingested_files(config, dataset, repo_root):
        raise FileNotFoundError(f"ingest stage is missing for {dataset.source}")
    output = feature_path(config, dataset, repo_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.with_suffix(".staging.parquet")
    staging.unlink(missing_ok=True)
    origin_path = output.parent / "origin-windows.parquet"
    destination_path = output.parent / "destination-windows.parquet"
    origin_staging = origin_path.with_suffix(".staging.parquet")
    destination_staging = destination_path.with_suffix(".staging.parquet")
    spill = repo_root / config.paths.work_dir / "spill" / dataset.source
    spill.mkdir(parents=True, exist_ok=True)
    with duckdb.connect() as connection:
        connection.execute(f"SET temp_directory={_sql_text(str(spill))}")
        materialize_feature_windows(
            connection,
            _timeline_sql(source),
            origin_staging,
            destination_staging,
            history_max=config.features.history_max,
            window_hours=config.features.window_hours,
            batch_rows=config.training.parquet_batch_rows,
        )
        origin_staging.replace(origin_path)
        destination_staging.replace(destination_path)
        query = _feature_sql(source, origin_path, destination_path)
        connection.execute(
            f"COPY ({query}) TO {_sql_text(str(staging))} "
            "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 65536)"
        )
        count_row = connection.execute(
            f"SELECT count(*) FROM read_parquet({_sql_text(str(staging))})"
        ).fetchone()
        if count_row is None:
            raise RuntimeError("DuckDB returned no feature-row count")
        row_count = int(count_row[0])
    staging.replace(output)
    result = FeatureBuild(
        candidate=dataset.candidate,
        source=dataset.source,
        row_count=row_count,
        feature_names=FEATURE_NAMES,
        parquet_sha256=_sha256(output),
    )
    atomic_write_model(feature_build_path(config, dataset, repo_root), result)
    return result


def load_feature_build(
    config: FullDataConfig, dataset: FullDataDataset, repo_root: Path
) -> FeatureBuild:
    """Load a validated feature-build provenance sidecar."""
    return FeatureBuild.model_validate_json(
        feature_build_path(config, dataset, repo_root).read_text(encoding="utf-8")
    )
