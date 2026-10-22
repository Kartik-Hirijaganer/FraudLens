"""Summary: Closed, shared validation for untrusted transaction uploads.

Key classes:
- (none)

Key functions:
- validate_upload: reject unsupported facts without rounding or inventing a timezone.
- identity_digest: derive tenant-scoped, domain-separated opaque identity keys.

Notes:
- Digests are pseudonyms, not proof of anonymisation or synthetic provenance.
- No uploaded model features are approved: scoring derives its own numeric features.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from fraudlens_core.schema import SchemaValidationError

CHANNELS = frozenset({"wire", "card", "ach", "swift", "crypto", "cash", "other"})
MODEL_CHANNELS = CHANNELS | frozenset(
    {
        "cash_deposit",
        "international_wire",
        "same_day_wire",
        "branch_cash",
        "peer_to_peer",
        "corporate_wire",
        "crypto_exchange",
        "business_wire",
        "outbound_wire",
    }
)
# Explicit supported subsets; internal dataset adapters retain their separate proxy contract.
CURRENCIES = frozenset(
    [
        "USD",
        "EUR",
        "GBP",
        "CAD",
        "AUD",
        "INR",
        "JPY",
        "CHF",
        "CNY",
        "BRL",
        "MXN",
        "RUB",
        "SAR",
        "ILS",
        "AED",
        "SGD",
        "HKD",
        "ZAR",
        "NZD",
        "KRW",
        "TRY",
        "SEK",
        "NOK",
        "DKK",
        "PLN",
    ]
)
COUNTRIES = frozenset(
    [
        "US",
        "CA",
        "MX",
        "BR",
        "AR",
        "CL",
        "CO",
        "PE",
        "GB",
        "IE",
        "FR",
        "DE",
        "ES",
        "PT",
        "IT",
        "NL",
        "BE",
        "LU",
        "CH",
        "AT",
        "SE",
        "NO",
        "DK",
        "FI",
        "IS",
        "PL",
        "CZ",
        "SK",
        "HU",
        "RO",
        "BG",
        "GR",
        "CY",
        "MT",
        "EE",
        "LV",
        "LT",
        "UA",
        "RU",
        "BY",
        "TR",
        "IL",
        "SA",
        "AE",
        "QA",
        "KW",
        "BH",
        "OM",
        "IN",
        "PK",
        "BD",
        "LK",
        "NP",
        "CN",
        "HK",
        "TW",
        "JP",
        "KR",
        "SG",
        "MY",
        "ID",
        "TH",
        "VN",
        "PH",
        "AU",
        "NZ",
        "ZA",
        "NG",
        "KE",
        "EG",
        "MA",
        "IR",
        "KP",
        "SY",
        "CU",
        "VE",
        "AF",
        "IQ",
        "MM",
        "KY",
        "SV",
        "PA",
    ]
)
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:\-]*", re.ASCII)
_MAX_IDENTIFIER_LENGTH = 128
_AMOUNT_LIMIT = Decimal("10000000000000000")
_CENT = Decimal("0.01")


def identity_digest(value: str, *, tenant: str, domain: str) -> str:
    """Hash exact identifiers using the existing SHA-256 fingerprint convention."""
    payload = json.dumps([domain, tenant, value], ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_upload(  # noqa: PLR0913 - explicit canonical transaction boundary.
    *,
    external_id: str,
    origin_account: str,
    dest_account: str,
    amount: Decimal | float | int | str,
    occurred_at: datetime,
    currency: str,
    country: str,
    channel: str,
) -> None:
    """Validate uploaded facts before shared canonical construction; never echo inputs."""
    for field, value in (
        ("external_id", external_id),
        ("origin_account", origin_account),
        ("dest_account", dest_account),
    ):
        if len(value) > _MAX_IDENTIFIER_LENGTH or not _IDENTIFIER.fullmatch(value):
            raise SchemaValidationError(field, "invalid_identifier")
    if currency.upper() not in CURRENCIES:
        raise SchemaValidationError("currency", "unsupported_currency")
    if country.upper() not in COUNTRIES:
        raise SchemaValidationError("country", "unsupported_country")
    if channel not in CHANNELS:
        raise SchemaValidationError("channel", "unsupported_channel")
    if occurred_at.utcoffset() is None:
        raise SchemaValidationError("occurred_at", "timezone_required")
    try:
        monetary_value = Decimal(str(amount))
        if not monetary_value.is_finite() or monetary_value <= 0 or monetary_value >= _AMOUNT_LIMIT:
            raise SchemaValidationError("amount", "out_of_range")
        if monetary_value != monetary_value.quantize(_CENT):
            raise SchemaValidationError("amount", "precision_exceeded")
    except (InvalidOperation, ValueError) as exc:
        raise SchemaValidationError("amount", "not_a_number") from exc
