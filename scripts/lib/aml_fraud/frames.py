"""Summary: Source frames, canonical columns, and bounded event-window primitives for AML data.

Key classes:
- IbmDemoTransaction: canonical IBM row paired with its deterministic tenant partition.

Key functions:
- load_frame: read only source columns consumed by the scorer.
- servable_frame: discard rows the canonical ingest boundary cannot serve.

Notes:
- Raw account identifiers exist only transiently for grouping and never leave this package.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from fraudlens_core import CanonicalTransaction
from lib.aml_mapping import (
    IEEE_EPOCH,
    ibm_account_key,
    ibm_channel,
    ibm_country,
    ieee_channel,
    ieee_country,
)

if TYPE_CHECKING:
    from fetch_dataset import DatasetPaths

IBM_AML = "ibm-aml"
IEEE_CIS = "ieee-cis"
_REAL_SOURCES: tuple[str, ...] = (IBM_AML, IEEE_CIS)

# Named IBM AML-Data column headers (no bare inline literals; governance rule 4).
_COL_TIMESTAMP = "Timestamp"
_COL_FROM_BANK = "From Bank"
_COL_FROM_ACCOUNT = "Account"
_COL_TO_BANK = "To Bank"
_COL_TO_ACCOUNT = "Account.1"
_COL_AMOUNT_PAID = "Amount Paid"
_COL_PAYMENT_CURRENCY = "Payment Currency"
_COL_PAYMENT_FORMAT = "Payment Format"
_COL_IS_LAUNDERING = "Is Laundering"

# Only the columns needed to compute the features (+ label). Amount Received, Receiving
# Currency, and everything else are intentionally dropped — the scorer dictates the inputs.
_IBM_KEEP_COLUMNS: tuple[str, ...] = (
    _COL_TIMESTAMP,
    _COL_FROM_BANK,
    _COL_FROM_ACCOUNT,
    _COL_TO_BANK,
    _COL_TO_ACCOUNT,
    _COL_AMOUNT_PAID,
    _COL_PAYMENT_CURRENCY,
    _COL_PAYMENT_FORMAT,
    _COL_IS_LAUNDERING,
)

# Optional IEEE-CIS training columns. Identity-table columns are excluded because the fixed
# scorer contract cannot reproduce them at inference.
_COL_IEEE_TIMESTAMP = "TransactionDT"
_COL_IEEE_AMOUNT = "TransactionAmt"
_COL_IEEE_CHANNEL = "ProductCD"
_COL_IEEE_ACCOUNT = "card1"
_COL_IEEE_COUNTRY = "addr2"
_COL_IEEE_LABEL = "isFraud"
_IEEE_KEEP_COLUMNS: tuple[str, ...] = (
    _COL_IEEE_TIMESTAMP,
    _COL_IEEE_AMOUNT,
    _COL_IEEE_CHANNEL,
    _COL_IEEE_ACCOUNT,
    _COL_IEEE_COUNTRY,
    _COL_IEEE_LABEL,
)

# Cent-precision round-amount modulus — the SAME check as features._is_round_amount (Decimal %
# 100); the anti-skew test pins this to extract_features so it can never silently diverge.
_ROUND_AMOUNT_MODULUS = Decimal("100")
# The canonical boundary quantizes amounts to cents (fraudlens_core.schema); training mirrors
# it so offline amounts equal the values the pipeline actually stores and scores. Sub-cent
# dust that rounds to zero cents is un-ingestable and therefore not servable training data.
_AMOUNT_QUANTUM = Decimal("0.01")
_WINDOW_SECONDS = 86_400.0  # the strict [t-24h, t) same-account window, in seconds.
# Burstiness sentinel when no prior exists in the 24h window (mirrors features.py).
_NO_PRIOR_SENTINEL_SECONDS = 86_400.0
# Mirrors the settings.investigation_history_max default: the online history query returns at
# most this many MOST RECENT rows, so offline windows must cap identically (anti-skew).
_DEFAULT_HISTORY_MAX = 100
_DEFAULT_DEMO_AGENCIES = 3  # recommended three-agency demo spread (tenancy).
_IEEE_EPOCH_SECONDS = IEEE_EPOCH.timestamp()
_DEMO_EXTERNAL_ID_PREFIX = "IBM-AML"
_DEMO_EXTERNAL_ID_DIGEST_LENGTH = 24

# --- Case-pack composition knobs (deterministic; named per governance rule 4) --------------
_CASE_PACK_CHUNK_ROWS = 1_000_000  # chunked CSV scan bound (memory-safe on the 5M-row file).
_ANCHOR_ROW_DIVISOR = 36  # ~one laundering anchor account per 36 budgeted pack rows.
_ANCHOR_MIN = 6  # small budgets still get a handful of laundering neighborhoods.
_NEIGHBORHOOD_WINDOW_SECONDS = 3 * 86_400.0  # ±3 days around an anchor's first laundering row.
_NEIGHBORHOOD_MAX_ROWS = 24  # per-anchor row cap keeps one busy account from eating the pack.
_BENIGN_OVERSAMPLE = 2  # benign stride candidates buffered vs the final benign quota.
# Default 60/20/20 anchor spread; index 0 is the primary (batch-scored) tenant. Callers that
# partition differently (the single-tenant portfolio demo) pass their own `tenant_weights`;
# this default keeps the offline GFP study path byte-for-byte unchanged.
_DEFAULT_TENANT_WEIGHTS: tuple[int, ...] = (0, 0, 0, 1, 2)
_LAUNDERING_LABEL = "1"
_BENIGN_LABEL = "0"


class IbmDemoTransaction(BaseModel):
    """A canonical IBM transaction paired with its deterministic tenant partition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    agency_index: int = Field(..., ge=0, description="Index into the configured demo agencies.")
    canonical: CanonicalTransaction = Field(..., description="Validated masked-ingest input.")


def _unsupported(source: str) -> ValueError:
    """Return a ValueError for a source this real-data loader does not build."""
    return ValueError(f"aml_fraud source must be one of {_REAL_SOURCES}, got '{source}'")


def load_frame(paths: DatasetPaths, source: str) -> pd.DataFrame:
    """Read the fetched dataset CSV, keeping only the columns needed for the features."""
    if source not in _REAL_SOURCES:
        raise _unsupported(source)
    csv_path = Path(paths.directory) / paths.files[0].name
    # dtype=str preserves exact amount/account text for the Decimal round-amount check + keys.
    columns = _IBM_KEEP_COLUMNS if source == IBM_AML else _IEEE_KEEP_COLUMNS
    return pd.read_csv(csv_path, usecols=list(columns), dtype=str)


_EPOCH = pd.Timestamp("1970-01-01", tz="UTC")


def _timestamps(frame: pd.DataFrame, source: str) -> pd.Series:
    """Return source timestamps as a UTC pandas Series."""
    if source == IBM_AML:
        return pd.Series(pd.to_datetime(frame[_COL_TIMESTAMP], utc=True), index=frame.index)
    if source == IEEE_CIS:
        elapsed = pd.to_numeric(frame[_COL_IEEE_TIMESTAMP], errors="raise")
        parsed = pd.to_datetime(elapsed + _IEEE_EPOCH_SECONDS, unit="s", utc=True)
        return pd.Series(parsed, index=frame.index)
    raise _unsupported(source)


def _occurred_seconds(frame: pd.DataFrame, source: str) -> np.ndarray:
    """Return each row's occurrence time as float epoch seconds (UTC), for the 24h window."""
    parsed = _timestamps(frame, source)
    return np.asarray((parsed - _EPOCH) // pd.Timedelta(seconds=1), dtype=float)


def _origin_keys(frame: pd.DataFrame, source: str) -> list[str]:
    """Return the transient source-account key used only for per-account windowing."""
    if source == IBM_AML:
        return [
            ibm_account_key(str(bank), str(account))
            for bank, account in zip(frame[_COL_FROM_BANK], frame[_COL_FROM_ACCOUNT], strict=True)
        ]
    if source == IEEE_CIS:
        return [str(account).strip() for account in frame[_COL_IEEE_ACCOUNT]]
    raise _unsupported(source)


def _dest_keys(frame: pd.DataFrame, source: str) -> list[str] | None:
    """Return the transient destination-account keys (None when the source has no dest side)."""
    if source == IBM_AML:
        return [
            ibm_account_key(str(bank), str(account))
            for bank, account in zip(frame[_COL_TO_BANK], frame[_COL_TO_ACCOUNT], strict=True)
        ]
    if source == IEEE_CIS:
        return None  # card-stream source: no counterparty side; dest features take defaults.
    raise _unsupported(source)


def _quantized_amount(value: Any) -> Decimal:
    """Quantize a raw amount to cents exactly like the canonical ingest boundary."""
    return Decimal(str(value)).quantize(_AMOUNT_QUANTUM, rounding=ROUND_HALF_UP)


def _amount_column(source: str) -> str:
    """Return the raw amount column for a source."""
    if source == IBM_AML:
        return _COL_AMOUNT_PAID
    if source == IEEE_CIS:
        return _COL_IEEE_AMOUNT
    raise _unsupported(source)


def servable_frame(frame: pd.DataFrame, source: str) -> pd.DataFrame:
    """Drop rows the canonical ingest boundary would reject (amount rounds to zero cents).

    Real IBM AML data carries sub-cent fx/crypto dust; such rows can never exist in a served
    database (ingest rejects them), so training on them would be train/serve skew.
    """
    column = _amount_column(source)
    keep = [index for index, value in enumerate(frame[column]) if _quantized_amount(value) > 0]
    if len(keep) == len(frame):
        return frame.reset_index(drop=True)
    return frame.iloc[keep].reset_index(drop=True)


def _source_values(
    frame: pd.DataFrame, source: str
) -> tuple[list[Decimal], list[str], list[str], np.ndarray]:
    """Return source amounts/tokens/labels through the shared canonical mapping functions.

    Amounts are cent-quantized exactly like the ingest boundary, so offline features equal the
    values the pipeline stores and scores (anti-skew).
    """
    if source == IBM_AML:
        amounts = [_quantized_amount(value) for value in frame[_COL_AMOUNT_PAID]]
        countries = [ibm_country(value) for value in frame[_COL_PAYMENT_CURRENCY]]
        channels = [ibm_channel(value) for value in frame[_COL_PAYMENT_FORMAT]]
        labels = np.asarray(frame[_COL_IS_LAUNDERING].astype(int), dtype=int)
        return amounts, countries, channels, labels
    if source == IEEE_CIS:
        amounts = [_quantized_amount(value) for value in frame[_COL_IEEE_AMOUNT]]
        countries = [ieee_country(value) for value in frame[_COL_IEEE_COUNTRY]]
        channels = [ieee_channel(value) for value in frame[_COL_IEEE_CHANNEL]]
        labels = np.asarray(frame[_COL_IEEE_LABEL].astype(int), dtype=int)
        return amounts, countries, channels, labels
    raise _unsupported(source)


@dataclass(frozen=True)
class _EventStream:
    """One sorted per-account event universe (internal, transient; never leaves the module)."""

    account: np.ndarray  # int codes, sorted primary key
    times: np.ndarray  # float seconds, sorted within account
    starts: dict[int, tuple[int, int]]  # account code -> [start, end) segment
    prefix_amount: np.ndarray  # cumsum with leading 0 (range sums via prefix[hi]-prefix[lo])
    prefix_inbound: np.ndarray
    prefix_inbound_amount: np.ndarray
    prefix_round: np.ndarray
    country: np.ndarray  # int token codes, sorted like times
    channel: np.ndarray


def _build_stream(  # noqa: PLR0913 - eight parallel event columns, assembled once (keyword-only)
    *,
    account_codes: np.ndarray,
    times: np.ndarray,
    row_ids: np.ndarray,
    amounts: np.ndarray,
    inbound: np.ndarray,
    is_round: np.ndarray,
    country_codes: np.ndarray,
    channel_codes: np.ndarray,
) -> _EventStream:
    """Sort events by (account, time, source row) and precompute the window prefix sums.

    The SOURCE-ROW tie-break gives equal-time events one canonical order regardless of the
    event's role (outbound vs inbound), so the most-recent cap selects the same rows the
    reference (file-ordered) online history would.
    """
    order = np.lexsort((row_ids, times, account_codes))
    account_sorted = account_codes[order]
    times_sorted = times[order]
    amounts_sorted = amounts[order]
    inbound_sorted = inbound[order]
    round_sorted = is_round[order]
    unique_accounts, first_index = np.unique(account_sorted, return_index=True)
    boundaries = np.append(first_index, account_sorted.shape[0])
    starts = {
        int(code): (int(boundaries[i]), int(boundaries[i + 1]))
        for i, code in enumerate(unique_accounts)
    }

    def _prefix(values: np.ndarray) -> np.ndarray:
        return np.concatenate(([0.0], np.cumsum(values)))

    return _EventStream(
        account=account_sorted,
        times=times_sorted,
        starts=starts,
        prefix_amount=_prefix(amounts_sorted),
        prefix_inbound=_prefix(inbound_sorted),
        prefix_inbound_amount=_prefix(amounts_sorted * inbound_sorted),
        prefix_round=_prefix(round_sorted),
        country=country_codes[order],
        channel=channel_codes[order],
    )


def _window_bounds(
    stream: _EventStream, account_code: int, query_times: np.ndarray, history_max: int
) -> tuple[np.ndarray, np.ndarray]:
    """Return per-query [lo, hi) indices of the capped [t-24h, t) window in the sorted stream.

    `side="left"` excludes equal-time events, matching the extractor's strict `< occurred_at`
    (and the SQL `occurred_at < before`). The cap keeps the MOST RECENT `history_max` events,
    matching the online `ORDER BY occurred_at DESC LIMIT n` query.
    """
    start, end = stream.starts.get(account_code, (0, 0))
    segment = stream.times[start:end]
    hi = start + np.searchsorted(segment, query_times, side="left")
    lo = start + np.searchsorted(segment, query_times - _WINDOW_SECONDS, side="left")
    lo = np.maximum(lo, hi - history_max)
    return lo, hi


def _distinct_in_window(tokens: np.ndarray, lo: int, hi: int, current_token: int) -> float:
    """Return the size of {current} plus window tokens (mirrors the extractor's set union)."""
    if hi <= lo:
        return 1.0
    window = tokens[lo:hi]
    distinct = np.unique(window)
    return float(distinct.shape[0] + (0 if current_token in distinct else 1))
