"""Summary: The canonical, framework-agnostic transaction schema shared by every
ingestion path (plan §16 Phase 3). `build_canonical` normalizes and validates a raw
transaction into a `CanonicalTransaction` — uppercasing the ISO currency/country codes,
rejecting a non-positive amount, an ill-formed code, or a future `occurred_at` — so the
API endpoint (`api/v1/transactions.py`) and the IEEE-CIS importer (`scripts/import_ieee.py`)
apply ONE definition of "valid" instead of re-implementing it (no duplication, rule 5).
`compute_feature_hash` derives a deterministic, PHI-free content fingerprint used as the
`transactions.feature_hash` (and later the hash-only `model_inference_logs`, ADR-015), so
the raw account identifiers never need to be stored to correlate a transaction. Living in
`fraudlens-core` keeps it importable by the backend, the importer, and `fraudlens-ml`
without crossing a layering boundary.

Key classes:
- SchemaValidationError: raised when a field fails canonical validation (carries no value).
- CanonicalTransaction: the normalized, validated domain transaction (snake_case).

Key functions:
- build_canonical: normalize + validate raw fields into a CanonicalTransaction.
- compute_feature_hash: deterministic PHI-free content fingerprint of a transaction.

Notes:
- Validation is structural for the ISO codes (3-letter currency / 2-letter country) — a
  documented subset, not a full code-list check — matching the Phase 3 risk note.
- SchemaValidationError never includes the offending value, only the field + a reason
  code, so it can flow into a 422 envelope or an importer rejection without leaking PHI.
- compute_feature_hash one-way-hashes the raw account identifiers into the fingerprint, so
  the output is unique per transaction yet contains no recoverable PHI and no agency id.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_MAX_FIELD_LEN = 128


class SchemaValidationError(Exception):
    """Raised when a transaction field fails canonical validation.

    Carries the offending ``field`` name and a machine-readable ``reason`` so the
    transport layer can build a 422 envelope (or the importer a rejection) without
    ever echoing the raw input value (FraudLens PHI hygiene).
    """

    def __init__(self, field: str, reason: str) -> None:
        """Store the field + reason and a static, value-free message."""
        self.field = field
        self.reason = reason
        super().__init__(f"invalid {field}: {reason}")


class CanonicalTransaction(BaseModel):
    """The normalized, validated domain representation of an ingested transaction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    external_id: str = Field(..., description="Caller-supplied unique id (dedup key).")
    amount: Decimal = Field(..., gt=0, description="Transaction amount (positive).")
    currency: str = Field(..., description="Normalized ISO-4217 currency code (upper).")
    occurred_at: datetime = Field(..., description="When the transaction occurred (tz-aware).")
    origin_account: str = Field(..., description="Raw origin account identifier (masked at rest).")
    dest_account: str = Field(..., description="Raw destination account identifier (masked).")
    channel: str = Field(..., description="Origination channel, e.g. 'wire' or 'card'.")
    country: str = Field(..., description="Normalized ISO-3166 alpha-2 country code (upper).")
    features: dict[str, Any] = Field(
        default_factory=dict, description="Additional model features (stored in features JSONB)."
    )


def _require_text(value: str, field: str) -> str:
    """Return a stripped non-empty value within the length cap, else raise."""
    text = value.strip()
    if not text:
        raise SchemaValidationError(field, "required")
    if len(text) > _MAX_FIELD_LEN:
        raise SchemaValidationError(field, "too_long")
    return text


# Canonical amount precision: the persisted `Numeric(18, 2)` column stores cents, so the
# boundary quantizes explicitly (half-up, matching Postgres numeric rounding) instead of
# letting storage truncate silently. An amount that rounds to zero cents is not a valid
# transaction for this system (real IBM AML data carries sub-cent fx/crypto dust that would
# otherwise persist as 0.00 and violate the downstream `amount > 0` analytical contract).
_AMOUNT_QUANTUM = Decimal("0.01")


def _coerce_amount(amount: Decimal | float | int | str) -> Decimal:
    """Coerce a raw amount to a positive cent-quantized Decimal, else raise."""
    try:
        value = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    except (InvalidOperation, ValueError) as exc:
        raise SchemaValidationError("amount", "not_a_number") from exc
    if not value.is_finite():
        raise SchemaValidationError("amount", "not_finite")
    if value <= 0:
        raise SchemaValidationError("amount", "not_positive")
    quantized = value.quantize(_AMOUNT_QUANTUM, rounding=ROUND_HALF_UP)
    if quantized <= 0:  # sub-half-cent dust rounds to 0.00 -> not storable as a transaction
        raise SchemaValidationError("amount", "not_positive")
    return quantized


def _require_aware(occurred_at: datetime, now: datetime) -> datetime:
    """Return a tz-aware occurred_at that is not in the future, else raise."""
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=UTC)
    if occurred_at > now:
        raise SchemaValidationError("occurred_at", "in_future")
    return occurred_at


def build_canonical(  # noqa: PLR0913 - a transaction has many fields; all are keyword-only
    *,
    external_id: str,
    amount: Decimal | float | int | str,
    currency: str,
    occurred_at: datetime,
    origin_account: str,
    dest_account: str,
    channel: str,
    country: str,
    features: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> CanonicalTransaction:
    """Normalize + validate raw fields into a CanonicalTransaction (raises on any failure)."""
    reference = now or datetime.now(UTC)
    currency_norm = currency.strip().upper()
    if not _CURRENCY_RE.match(currency_norm):
        raise SchemaValidationError("currency", "not_iso4217")
    country_norm = country.strip().upper()
    if not _COUNTRY_RE.match(country_norm):
        raise SchemaValidationError("country", "not_iso3166")
    return CanonicalTransaction(
        external_id=_require_text(external_id, "external_id"),
        amount=_coerce_amount(amount),
        currency=currency_norm,
        occurred_at=_require_aware(occurred_at, reference),
        origin_account=_require_text(origin_account, "origin_account"),
        dest_account=_require_text(dest_account, "dest_account"),
        channel=_require_text(channel, "channel"),
        country=country_norm,
        features=features or {},
    )


def compute_feature_hash(canonical: CanonicalTransaction) -> str:
    """Return a deterministic, PHI-free SHA-256 fingerprint of a transaction's content.

    The raw account identifiers are folded in one-way (their own SHA-256) so two
    transactions that differ only by account differ in the fingerprint, while the
    output reveals no recoverable PHI and never includes the tenant id (ADR-015).
    """
    payload: dict[str, Any] = {
        "amount": format(canonical.amount.normalize(), "f"),
        "currency": canonical.currency,
        "occurredAt": canonical.occurred_at.astimezone(UTC).isoformat(),
        "channel": canonical.channel,
        "country": canonical.country,
        "features": canonical.features,
        "accounts": hashlib.sha256(
            f"{canonical.origin_account}\x1f{canonical.dest_account}".encode()
        ).hexdigest(),
    }
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
