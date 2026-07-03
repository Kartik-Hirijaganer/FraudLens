"""Summary: Pydantic request/response models for the transaction ingestion surface
(plan §5.4, §16 Phase 3). Every model is a `CamelModel`, so the wire is camelCase while
Python stays snake_case, and `extra="forbid"` rejects unknown fields. The request DTO
(`TransactionIngestRequest`) carries only structural constraints (positive amount, code
lengths); the semantic normalization + validation (ISO codes, not-future `occurredAt`) and
the feature hash run once in `fraudlens_core.build_canonical`, shared with the importer.
Responses expose the MASKED account identifiers only (raw PHI is never stored or returned,
ADR-014). Batch/CSV responses report accepted/duplicate/rejected counts plus a bounded list
of PHI-free per-row rejections so a partial upload tells the caller exactly what failed.

Key classes:
- TransactionIngestRequest: one transaction to ingest (camelCase DTO, structural checks).
- TransactionResponse: a persisted transaction (masked accounts, null riskBand at ingest).
- BatchIngestRequest: a batch of transactions (+ dryRun to validate without persisting).
- IngestRejection: a single PHI-free per-row rejection (index, code, message).
- BatchIngestResponse: batch outcome (counts + created rows + bounded sampleErrors).
- CsvUploadResponse: CSV upload outcome (jobId + counts + bounded sampleErrors).
- TransactionListResponse: a page of transactions plus the opaque nextCursor + total count.
- ClientErrorReport: the frontend client-error sink body (message + safe context).

Key functions:
- (none)

Notes:
- amount is a Decimal and serializes as a JSON string (financial precision is preserved).
- IngestRejection carries a code + fixed message + optional externalId only — never the
  rejected row's values — so a malformed upload cannot echo PHI back to the caller.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import Field

from fraudlens_backend.models.common import CamelModel

_MAX_FIELD_LEN = 128


class TransactionIngestRequest(CamelModel):
    """One transaction to ingest; structural checks only (semantics in build_canonical)."""

    external_id: str = Field(
        ..., min_length=1, max_length=_MAX_FIELD_LEN, description="Caller-unique id (dedup key)."
    )
    amount: Decimal = Field(..., gt=0, description="Transaction amount (must be positive).")
    currency: str = Field(..., min_length=3, max_length=3, description="ISO-4217 currency code.")
    occurred_at: datetime = Field(..., description="When the transaction occurred (not future).")
    origin_account: str = Field(
        ..., min_length=1, max_length=_MAX_FIELD_LEN, description="Origin account (masked at rest)."
    )
    dest_account: str = Field(
        ..., min_length=1, max_length=_MAX_FIELD_LEN, description="Destination account (masked)."
    )
    channel: str = Field(
        ..., min_length=1, max_length=_MAX_FIELD_LEN, description="Origination channel."
    )
    country: str = Field(..., min_length=2, max_length=2, description="ISO-3166 alpha-2 country.")
    features: dict[str, object] = Field(
        default_factory=dict, description="Optional extra model features (stored in JSONB)."
    )


class TransactionResponse(CamelModel):
    """A persisted transaction; account identifiers are masked, riskBand null at ingest."""

    transaction_id: str = Field(..., description="The transaction's unique id (UUID).")
    external_id: str = Field(..., description="Caller-supplied unique id.")
    agency_id: str = Field(..., description="Owning tenant (agency) id.")
    amount: Decimal = Field(..., description="Transaction amount.")
    currency: str = Field(..., description="ISO-4217 currency code.")
    occurred_at: datetime = Field(..., description="When the transaction occurred.")
    origin_account: str = Field(..., description="Masked origin account identifier.")
    dest_account: str = Field(..., description="Masked destination account identifier.")
    channel: str = Field(..., description="Origination channel.")
    country: str = Field(..., description="ISO-3166 alpha-2 country code.")
    risk_band: str | None = Field(default=None, description="Risk band (null until scored).")
    latest_run_id: str | None = Field(default=None, description="Latest analysis run id, if any.")
    ingested_at: datetime = Field(..., description="When the transaction was ingested.")


class BatchIngestRequest(CamelModel):
    """A batch of transactions; dryRun validates + masks without persisting anything.

    Items are accepted as raw camelCase objects (the `TransactionIngestRequest` shape)
    and validated PER ROW in the handler, so one malformed item becomes a rejection
    rather than failing the whole request — i.e. the batch is partial-accept.
    """

    transactions: list[dict[str, Any]] = Field(
        ..., min_length=1, description="Transactions to ingest (non-empty; validated per row)."
    )
    dry_run: bool = Field(
        default=False, description="When true, validate + mask but persist nothing."
    )


class IngestRejection(CamelModel):
    """A single PHI-free per-row rejection from a batch/CSV ingest."""

    index: int = Field(..., ge=0, description="Zero-based position of the rejected row.")
    external_id: str | None = Field(
        default=None, description="The row's externalId when known (no other values)."
    )
    code: str = Field(..., description="Stable error code (e.g. 'duplicate_external_id').")
    message: str = Field(..., description="Fixed, PHI-free reason for the rejection.")


class BatchIngestResponse(CamelModel):
    """Outcome of a batch ingest: counts, created rows, and bounded sample errors."""

    accepted: int = Field(..., ge=0, description="Rows inserted (0 on dryRun).")
    duplicates: int = Field(..., ge=0, description="Rows skipped as already-existing.")
    rejected: int = Field(..., ge=0, description="Rows rejected as invalid.")
    dry_run: bool = Field(..., description="Whether this was a non-persisting validation run.")
    transactions: list[TransactionResponse] = Field(
        default_factory=list, description="Created transactions (empty on dryRun)."
    )
    sample_errors: list[IngestRejection] = Field(
        default_factory=list, description="Bounded sample of per-row rejections (PHI-free)."
    )


class CsvUploadResponse(CamelModel):
    """Outcome of a CSV upload: the recorded job id, counts, and bounded sample errors."""

    job_id: str = Field(..., description="The csv_import job_executions id for this upload.")
    accepted: int = Field(..., ge=0, description="Rows inserted.")
    duplicates: int = Field(..., ge=0, description="Rows skipped as already-existing.")
    rejected: int = Field(..., ge=0, description="Rows rejected as invalid.")
    sample_errors: list[IngestRejection] = Field(
        default_factory=list, description="Bounded sample of per-row rejections (PHI-free)."
    )


class TransactionListResponse(CamelModel):
    """A page of transactions plus the opaque next-page cursor and the total matching count."""

    transactions: list[TransactionResponse] = Field(
        default_factory=list, description="The transactions on this page (masked)."
    )
    next_cursor: str | None = Field(
        default=None, description="Opaque cursor for the next page, or null when exhausted."
    )
    total: int = Field(
        default=0, ge=0, description="Total rows matching the filters, across all pages."
    )


class ClientErrorReport(CamelModel):
    """The frontend client-error sink body (PHI-scrubbed before logging)."""

    message: str = Field(..., min_length=1, description="Client error message (scrubbed).")
    context: dict[str, str] | None = Field(
        default=None, description="Optional safe key/value context (scrubbed)."
    )
