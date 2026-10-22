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
- UploadFeatures: closed empty upload feature contract.
- TransactionIngestRequest: one transaction to ingest (camelCase DTO, structural checks).
- InvestigationSummary:
- ScreeningProgress:
- TransactionResponse: a persisted transaction (masked accounts, null riskBand at ingest).
- BatchIngestRequest: a batch of transactions (+ dryRun to validate without persisting).
- IngestRejection: a single PHI-free per-row rejection (index, code, message).
- BatchIngestResponse: batch outcome (counts + created rows + bounded sampleErrors).
- CsvUploadResponse: CSV upload outcome (jobId + counts + bounded sampleErrors).
- CsvTemplateResponse:
- TransactionListResponse: a page of transactions plus the opaque nextCursor + total count.
- ClientErrorReport: the frontend client-error sink body (message + safe context).

Key functions:
- (none)

Notes:
- amount is a Decimal and serializes as a JSON string (financial precision is preserved).
- IngestRejection carries a code, fixed message and row position; externalId is always null.
- Upload identifiers become opaque references; account displays are fully withheld.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field

from fraudlens_backend.models.common import CamelModel

_MAX_FIELD_LEN = 128


class UploadFeatures(CamelModel):
    """Closed feature contract: upload features are derived server-side, never caller text."""


class TransactionIngestRequest(CamelModel):
    """One transaction to ingest; structural checks only (semantics in build_canonical)."""

    external_id: str = Field(
        ...,
        min_length=1,
        max_length=_MAX_FIELD_LEN,
        description="Caller-unique dedup key; persisted and displayed as an opaque reference.",
    )
    amount: Decimal = Field(
        ...,
        gt=0,
        description="Positive finite exact-cent amount fitting Numeric(18,2); never rounded.",
    )
    currency: str = Field(..., min_length=3, max_length=3, description="ISO-4217 currency code.")
    occurred_at: datetime = Field(
        ..., description="ISO-8601 occurrence with an explicit timezone; cannot be future."
    )
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
    features: UploadFeatures = Field(
        default_factory=UploadFeatures,
        description="Closed empty object; features are derived server-side.",
    )


class InvestigationSummary(CamelModel):
    """Saved or active run selected for read-only transaction navigation."""

    run_id: str = Field(..., description="Existing investigation to open.")
    status: str = Field(..., description="Persisted investigation status.")
    error_code: str | None = Field(default=None, description="Safe core failure reason.")


class ScreeningProgress(CamelModel):
    """Durable automatic-screening counts; blocked drafting is a subset of completion."""

    total: int = Field(default=0, ge=0, description="Accepted transactions queued for screening.")
    pending: int = Field(default=0, ge=0, description="Runs waiting for their first worker.")
    running: int = Field(default=0, ge=0, description="Runs currently executing.")
    retrying: int = Field(default=0, ge=0, description="Runs waiting for bounded recovery.")
    completed: int = Field(
        default=0, ge=0, description="Runs with completed deterministic analysis."
    )
    failed: int = Field(default=0, ge=0, description="Runs with failed deterministic analysis.")
    drafting_blocked: int = Field(
        default=0, ge=0, description="Completed runs whose latest SAR failed or was blocked."
    )
    poll_after_ms: int = Field(..., gt=0, description="Configured progress polling interval.")


class TransactionResponse(CamelModel):
    """A persisted transaction; account identifiers are masked, riskBand null at ingest."""

    transaction_id: str = Field(..., description="The transaction's unique id (UUID).")
    external_id: str = Field(
        ..., description="Opaque upload reference; trusted imports retain their source id."
    )
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
    investigation: InvestigationSummary | None = Field(
        default=None, description="Existing run for read-only navigation."
    )
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
    row_number: int | None = Field(
        default=None,
        ge=2,
        description="Starting physical CSV line including header; null for JSON batches.",
    )
    external_id: str | None = Field(
        default=None, description="Always null; uploaded identifiers are never reflected in errors."
    )
    code: str = Field(..., description="Stable error code (e.g. 'duplicate_external_id').")
    reason_code: str | None = Field(
        default=None, description="Stable value-free canonical reason, such as precision_exceeded."
    )
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
    trusted_synthetic: bool = Field(
        default=False, description="Uploaded bytes match the bundled server-owned synthetic CSV."
    )
    drafting_mode: Literal["template", "mock", "ai"] = Field(
        default="template",
        description="Drafting route for newly accepted flagged rows, not a quality verdict.",
    )
    screening: ScreeningProgress | None = Field(
        default=None, description="Initial background-screening progress."
    )
    accepted: int = Field(..., ge=0, description="Rows inserted.")
    duplicates: int = Field(..., ge=0, description="Rows skipped as already-existing.")
    rejected: int = Field(..., ge=0, description="Rows rejected as invalid.")
    sample_errors: list[IngestRejection] = Field(
        default_factory=list, description="Bounded sample of per-row rejections (PHI-free)."
    )


class CsvTemplateResponse(CamelModel):
    """The exact synthetic CSV to save and upload unchanged."""

    content: str = Field(..., description="UTF-8 CSV contents; changes invalidate synthetic trust.")


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
