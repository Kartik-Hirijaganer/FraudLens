"""Summary: Shared CSV decoding and canonical ingestion validation helpers.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- Error samples never echo rejected field values.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from fastapi import Request

from fraudlens_backend.models.errors import AppError
from fraudlens_backend.models.transactions import IngestRejection
from fraudlens_core import CanonicalTransaction, SchemaValidationError, build_canonical

_CSV_CONTENT_TYPES = ("text/csv", "application/csv", "application/vnd.ms-excel")
_CANONICAL_KEYS = frozenset(
    {
        "externalId",
        "amount",
        "currency",
        "occurredAt",
        "originAccount",
        "destAccount",
        "channel",
        "country",
    }
)


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

    Unknown columns are folded into `features`; `build_canonical` owns all semantic
    validation, so every ingest path enforces the same contract (no duplication).
    """

    def required(name: str) -> Any:
        value = data.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise SchemaValidationError(name, "required")
        return value

    features = dict(data.get("features") or {})
    features.update(
        {
            key: value
            for key, value in data.items()
            if key not in _CANONICAL_KEYS and key != "features" and value is not None
        }
    )
    return build_canonical(
        external_id=str(required("externalId")),
        amount=required("amount"),
        currency=str(required("currency")),
        occurred_at=_coerce_datetime(required("occurredAt")),
        origin_account=str(required("originAccount")),
        dest_account=str(required("destAccount")),
        channel=str(required("channel")),
        country=str(required("country")),
        features=features,
    )


def _append_sample(
    samples: list[IngestRejection],
    index: int,
    external_id: str | None,
    exc: SchemaValidationError,
    limit: int,
) -> None:
    """Append a bounded, PHI-free rejection sample for an invalid row."""
    if len(samples) < limit:
        samples.append(
            IngestRejection(
                index=index,
                external_id=external_id,
                code="validation_error",
                message=f"{exc.field}: {exc.reason}",
            )
        )


async def _read_csv(request: Request, max_bytes: int, max_rows: int) -> list[dict[str, Any]]:
    """Read + parse the raw CSV body, enforcing content-type and the size/row caps."""
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type and content_type not in _CSV_CONTENT_TYPES:
        raise AppError("unsupported_content_type")
    body = await request.body()
    if len(body) > max_bytes:
        raise AppError("payload_too_large")
    reader = csv.DictReader(io.StringIO(body.decode("utf-8", errors="replace")))
    if reader.fieldnames is None:
        raise AppError("invalid_csv")
    rows = list(reader)
    if len(rows) > max_rows:
        raise AppError("too_many_rows")
    if not rows:
        raise AppError("empty_payload")
    return rows


def _as_str(value: Any) -> str | None:
    """Return a string for a known externalId (for a PHI-free rejection), else None."""
    return str(value) if isinstance(value, str) and value else None
