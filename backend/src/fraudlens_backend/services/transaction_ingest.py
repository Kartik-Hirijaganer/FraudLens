"""Summary: Shared CSV decoding and canonical ingestion validation helpers.

Key classes:
- (none)

Key functions:
- synthetic_csv_template:

Notes:
- Error samples never echo rejected field values.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from fastapi import Request

from fraudlens_backend.models.errors import AppError
from fraudlens_backend.models.transactions import IngestRejection
from fraudlens_backend.services.csv_validation import TRANSACTION_COLUMNS, CsvDocument, parse_csv
from fraudlens_backend.settings import find_config_dir
from fraudlens_core import CanonicalTransaction, SchemaValidationError, build_canonical
from fraudlens_core.upload_validation import validate_upload

_CSV_CONTENT_TYPES = ("text/csv", "application/csv", "application/vnd.ms-excel")
_CANONICAL_KEYS = frozenset(TRANSACTION_COLUMNS)


def synthetic_csv_template() -> bytes:
    """Read the single reviewed demo fixture bundled with backend configuration."""
    return (find_config_dir() / "demo-transactions.csv").read_bytes()


def _coerce_datetime(value: Any) -> datetime:
    """Return a datetime from a datetime or an ISO-8601 string, else raise."""
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise SchemaValidationError("occurredAt", "not_a_datetime") from exc


def _mapping_to_canonical(data: Mapping[str, Any]) -> CanonicalTransaction:
    """Validate one camelCase row (single/batch/CSV) into a CanonicalTransaction (may raise).

    Uploaded features are closed: no caller-supplied model features are approved.
    Shared upload validation rejects lossy financial conversions before canonicalization.
    """

    def required(name: str) -> Any:
        value = data.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise SchemaValidationError(name, "required")
        return value

    if set(data) - _CANONICAL_KEYS - {"features"}:
        raise SchemaValidationError("columns", "unexpected_fields")
    if data.get("features", {}) != {}:
        raise SchemaValidationError("features", "unsupported_features")
    for key, value in data.items():
        if key not in {"features", "amount", "occurredAt"} and not isinstance(value, str):
            raise SchemaValidationError("transaction", "invalid_type")
    occurred_at = _coerce_datetime(required("occurredAt"))
    validate_upload(
        external_id=required("externalId"),
        origin_account=required("originAccount"),
        dest_account=required("destAccount"),
        amount=required("amount"),
        occurred_at=occurred_at,
        currency=required("currency"),
        country=required("country"),
        channel=required("channel"),
    )
    return build_canonical(
        external_id=str(required("externalId")),
        amount=required("amount"),
        currency=str(required("currency")),
        occurred_at=occurred_at,
        origin_account=str(required("originAccount")),
        dest_account=str(required("destAccount")),
        channel=str(required("channel")),
        country=str(required("country")),
        features={},
    )


def _append_sample(
    samples: list[IngestRejection],
    index: int,
    exc: SchemaValidationError,
    limit: int,
    *,
    row_number: int | None = None,
) -> None:
    """Append a bounded, PHI-free rejection sample for an invalid row."""
    if len(samples) < limit:
        samples.append(
            IngestRejection(
                index=index,
                external_id=None,
                row_number=row_number,
                code="validation_error",
                reason_code=exc.reason,
                message=f"{exc.field}: {exc.reason}",
            )
        )


async def _read_csv(
    request: Request,
    max_bytes: int,
    max_rows: int,
    max_columns: int = 8,
    max_field_chars: int = 128,
) -> CsvDocument:
    """Bound the stream before buffering, then parse with closed headers and field caps."""
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type and content_type not in _CSV_CONTENT_TYPES:
        raise AppError("unsupported_content_type")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_bytes:
            raise AppError("payload_too_large")
        body.extend(chunk)
    rows = parse_csv(
        bytes(body), max_rows=max_rows, max_columns=max_columns, max_field_chars=max_field_chars
    )
    try:
        trusted = bytes(body) == synthetic_csv_template()
    except OSError:
        trusted = False
    return CsvDocument(rows=rows, trusted_synthetic=trusted)
