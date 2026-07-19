"""Summary: The single source of truth for mapping a raw public-dataset row onto the
canonical channel / country / direction tokens the FraudLens scorer understands (real-AML
training plan Phase 1). Both the real-data trainer (IBM AML-Data track) and the IEEE-CIS
importer (`scripts/import_ieee.py`) run their rows through THESE mappers, so a model trains
on the exact same graded `channel_risk`/`country_risk` distribution it later serves — the
anti-skew principle. The mappers emit only PHI-free proxy tokens (a channel string, an
ISO-3166 alpha-2 country, an is_outbound flag, a windowing account key); the numeric risk
grading itself stays in `fraudlens_ml.scoring.features` and is applied by the caller, never
re-implemented here. Unknown/blank inputs fall back to documented defaults so a mapper never
raises on an unseen value.

Key classes:
- (none)

Key functions:
- ibm_channel: map an IBM AML-Data `Payment Format` to a canonical channel token.
- ibm_currency: map an IBM AML-Data currency name to a canonical ISO-4217 code.
- ibm_country: map an IBM AML-Data currency name to a proxy ISO-3166 alpha-2 country.
- ibm_is_outbound: map an account's send/receive role in a transfer to the is_outbound flag.
- ibm_account_key: build the PHI-transient `Bank+Account` key used to group the 24h window.
- ieee_channel: map an IEEE-CIS `ProductCD` to a canonical channel token.
- ieee_country: map an IEEE-CIS `addr2` code to a proxy ISO-3166 alpha-2 country.

Notes:
- Channel tokens are the scorer's `_CHANNEL_RISK` keys (card/ach/wire/swift/crypto/cash);
  anything else (Cheque, Reinvestment, unknown) maps to the default token, which
  `channel_risk` resolves to its documented default weight — no lie that it is ACH/wire.
- IBM country is proxied from the transaction currency (the dataset has no country column);
  the eurozone "Euro" uses Germany (DE) as a documented representative, and borderless
  Bitcoin / any unknown currency map to the ISO "unknown" code ZZ (scorer default risk).
- IEEE-CIS is US-dollar / US-centric, so its country default is US (matching the importer's
  historical documented default); only a recognized `addr2` overrides it.
- IEEE_EPOCH is the reference instant IEEE-CIS `TransactionDT` counts seconds from; it lives
  here so the importer and any IEEE feature builder share one definition.
"""

from __future__ import annotations

from datetime import UTC, datetime

# --- Canonical fallback tokens (named, no magic values; governance rule 4) ---------------
# Not one of the scorer's six graded channels, so `channel_risk` applies its documented
# default weight — an honest "we don't grade this channel" rather than mislabeling it.
_DEFAULT_CHANNEL_TOKEN = "other"
# ISO 3166 user-assigned code reserved for "unknown"; passes the canonical country regex and
# resolves to the scorer's default country risk without pretending to be a real jurisdiction.
_UNKNOWN_COUNTRY_TOKEN = "ZZ"
# ISO-4217 code for transactions where no currency is applicable/known. This preserves the
# canonical three-letter contract without inventing a real jurisdictional currency.
_UNKNOWN_CURRENCY_TOKEN = "XXX"

# --- IBM AML-Data (AMLworld) mappers ------------------------------------------------------
# `Payment Format` -> the scorer's graded channel token. Cheque/Reinvestment are real bank
# instruments but not among the six graded channels, so they take the default token.
_IBM_PAYMENT_FORMAT_CHANNELS: dict[str, str] = {
    "ach": "ach",
    "wire": "wire",
    "cash": "cash",
    "credit card": "card",
    "bitcoin": "crypto",
    "cheque": _DEFAULT_CHANNEL_TOKEN,
    "reinvestment": _DEFAULT_CHANNEL_TOKEN,
}
# Currency name -> a representative ISO-3166 alpha-2 country used as the country proxy (the
# dataset carries currency, not country). Codes present in the scorer's `_COUNTRY_RISK` grade
# meaningfully; the rest fall through to its documented default weight.
_IBM_CURRENCY_COUNTRIES: dict[str, str] = {
    "us dollar": "US",
    "euro": "DE",
    "uk pound": "GB",
    "canadian dollar": "CA",
    "australian dollar": "AU",
    "brazil real": "BR",
    "mexican peso": "MX",
    "ruble": "RU",
    "yuan": "CN",
    "yen": "JP",
    "rupee": "IN",
    "swiss franc": "CH",
    "saudi riyal": "SA",
    "shekel": "IL",
    "bitcoin": _UNKNOWN_COUNTRY_TOKEN,
}
_IBM_CURRENCY_CODES: dict[str, str] = {
    "us dollar": "USD",
    "euro": "EUR",
    "uk pound": "GBP",
    "canadian dollar": "CAD",
    "australian dollar": "AUD",
    "brazil real": "BRL",
    "mexican peso": "MXN",
    "ruble": "RUB",
    "yuan": "CNY",
    "yen": "JPY",
    "rupee": "INR",
    "swiss franc": "CHF",
    "saudi riyal": "SAR",
    "shekel": "ILS",
    "bitcoin": "XBT",
}
# Joins Bank + Account into one windowing key; the ids are used only transiently to group an
# account's 24h history and are then discarded (the emitted feature matrix is PHI-free).
_ACCOUNT_KEY_SEPARATOR = "\x1f"

_OUTBOUND = 1.0
_INBOUND = 0.0

# --- IEEE-CIS (optional secondary track) mappers ------------------------------------------
# Reference epoch: IEEE-CIS `TransactionDT` is seconds elapsed after this instant.
IEEE_EPOCH = datetime(2017, 12, 1, tzinfo=UTC)
# `ProductCD` is an opaque product code; this is a documented PROXY onto graded channels so
# the optional IEEE track's `channel_risk` is not degenerate (the primary track is IBM).
_IEEE_PRODUCT_CD_CHANNELS: dict[str, str] = {
    "w": "card",
    "c": "card",
    "r": "ach",
    "h": "wire",
    "s": "cash",
}
# IEEE-CIS is US-centric; `addr2` is a coded country whose dominant value (87) is the US.
_IEEE_DEFAULT_COUNTRY = "US"
_IEEE_ADDR2_COUNTRIES: dict[str, str] = {
    "87": "US",
}


def ibm_channel(payment_format: str | None) -> str:
    """Map an IBM AML-Data `Payment Format` to a canonical channel token (default if unknown)."""
    if payment_format is None:
        return _DEFAULT_CHANNEL_TOKEN
    return _IBM_PAYMENT_FORMAT_CHANNELS.get(payment_format.strip().lower(), _DEFAULT_CHANNEL_TOKEN)


def ibm_country(currency: str | None) -> str:
    """Map an IBM AML-Data currency name to a proxy ISO-3166 alpha-2 country (unknown -> ZZ)."""
    if currency is None:
        return _UNKNOWN_COUNTRY_TOKEN
    return _IBM_CURRENCY_COUNTRIES.get(currency.strip().lower(), _UNKNOWN_COUNTRY_TOKEN)


def ibm_currency(currency: str | None) -> str:
    """Map an IBM AML-Data currency name to an ISO-4217 code (unknown -> XXX)."""
    if currency is None:
        return _UNKNOWN_CURRENCY_TOKEN
    return _IBM_CURRENCY_CODES.get(currency.strip().lower(), _UNKNOWN_CURRENCY_TOKEN)


def ibm_is_outbound(is_sender: bool) -> float:
    """Return the is_outbound flag from an account's role: 1.0 as the sender, else 0.0."""
    return _OUTBOUND if is_sender else _INBOUND


def ibm_account_key(bank: str, account: str) -> str:
    """Build the transient `Bank+Account` key grouping an account's 24h window (PHI-transient)."""
    return f"{bank.strip()}{_ACCOUNT_KEY_SEPARATOR}{account.strip()}"


def ieee_channel(product_cd: str | None) -> str:
    """Map an IEEE-CIS `ProductCD` to a canonical channel token (default if unknown)."""
    if product_cd is None:
        return _DEFAULT_CHANNEL_TOKEN
    return _IEEE_PRODUCT_CD_CHANNELS.get(product_cd.strip().lower(), _DEFAULT_CHANNEL_TOKEN)


def ieee_country(addr2: str | None) -> str:
    """Map an IEEE-CIS `addr2` code to a proxy ISO-3166 alpha-2 country (default US if unknown)."""
    if addr2 is None or str(addr2).strip() == "":
        return _IEEE_DEFAULT_COUNTRY
    return _IEEE_ADDR2_COUNTRIES.get(str(addr2).strip(), _IEEE_DEFAULT_COUNTRY)
