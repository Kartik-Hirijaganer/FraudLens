"""Summary: The real AML-dataset training loader (real-AML plan Phases 3/4 + full-IBM plan
Phases 4/5), mirroring `synthetic_fraud.py`'s surface for IBM AML-Data and the optional IEEE-CIS
source. It turns a CSV into the SAME PHI-free, `FEATURE_NAMES`-ordered feature matrix the scorer
serves — the anti-skew principle: `build_feature_matrix` replicates
`fraudlens_ml.scoring.features.extract_features` semantics EXACTLY (the graded
`country_risk`/`channel_risk` lookups, the strict `[t-24h, t)` half-open window with equal-time
priors excluded, the `Decimal % 100` round-amount check, the direction-split and counterparty
fan-in v2 signals, AND the online `investigation_history_max` most-recent-rows cap), so offline
features equal what the live extractor sees per row. Raw account/bank/card ids are used only
transiently to group per-account event streams, then discarded — the matrix that leaves this
module is PHI-free (numeric features + the label). The split is CHRONOLOGICAL
(earliest→train, latest→calibration/holdout) and keeps an account's rows inside one fold. The
demo path ships `load_ibm_case_pack`: a deterministic, representative selection of complete
laundering-account time neighborhoods plus benign stride controls — replacing the old
all-negative CSV prefix — where the public label steers offline selection ONLY and is never
persisted or converted into an alert.

Key classes:
- IbmDemoTransaction: a canonical IBM row paired with its deterministic tenant partition.

Key functions:
- load_frame: read a fetched dataset CSV, keeping only the columns the features consume.
- servable_frame:
- build_feature_matrix: map the frame to (X in FEATURE_NAMES order, y) replicating the extractor.
- split_chronological: split (X, y) by time into train/calibration/holdout, accounts kept whole.
- source_columns: the raw dataset columns consumed for a source (the manifest's schema record).
- sample_frame: seeded, label-stratified subsample of a frame for fast local iteration.
- demo_agency_index: map a source bank to one of N demo agencies (demo-ingest tenancy seam).
- map_ibm_demo_row: map one IBM row to a canonical masked-ingest input and agency partition.
- load_ibm_case_pack: deterministic laundering-neighborhood + benign-control demo case pack.

Notes:
- is_outbound is 1.0 for every training row: the live pipeline scores the transaction under
review as OUTBOUND (build_pipeline_input); v2 direction signals instead come from the
windowed history (inbound velocity/amount) and the counterparty stream (dest fan-in).
- Window semantics mirror extract_features: velocity/inbound/round-share/fan-in count PRIORS
only; the amount sum, distinct countries/channels, and dest inbound sum INCLUDE the current
row; `seconds_since_prev` uses the most recent 24h prior (sentinel 86,400s when none).
- The online history query caps at `investigation_history_max` MOST RECENT rows; the builder
mirrors that cap per window, which the old two-pointer implementation ignored.
- Training is global while serving history is tenant-scoped; the case pack keeps each
laundering neighborhood inside one tenant so served windows match training windows.
- Column selection is dictated by the scorer, not chosen freely; unknown categoricals take the
scorer's documented defaults (never NaN). XGBoost needs no scaling.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from fraudlens_core import CanonicalTransaction, SchemaValidationError, build_canonical
from fraudlens_ml.scoring.features import FEATURE_NAMES, channel_risk, country_risk
from lib.aml_mapping import (
    IEEE_EPOCH,
    ibm_account_key,
    ibm_channel,
    ibm_country,
    ibm_currency,
    ibm_is_outbound,
    ieee_channel,
    ieee_country,
)
from lib.dataset import _CALIBRATION_FRACTION, _HOLDOUT_FRACTION, DataSplit

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


def build_feature_matrix(
    frame: pd.DataFrame, source: str, *, history_max: int = _DEFAULT_HISTORY_MAX
) -> tuple[np.ndarray, np.ndarray]:
    """Map the frame to (X in FEATURE_NAMES order, y), replicating extract_features exactly.

    `history_max` mirrors `settings.investigation_history_max` — the online cap on history rows
    per window — so offline features equal what the live extractor is actually fed.
    """
    amounts_decimal, countries, channels, labels = _source_values(frame, source)
    amounts = np.array([float(value) for value in amounts_decimal])
    is_round = np.array(
        [1.0 if value % _ROUND_AMOUNT_MODULUS == 0 else 0.0 for value in amounts_decimal]
    )
    seconds = _occurred_seconds(frame, source)
    parsed = _timestamps(frame, source)
    n = len(frame)

    origin_keys = _origin_keys(frame, source)
    dest_keys = _dest_keys(frame, source)
    country_codes = pd.factorize(np.asarray(countries, dtype=object))[0]
    channel_codes = pd.factorize(np.asarray(channels, dtype=object))[0]

    # One shared account-code space across both roles so a key is the same account everywhere.
    all_keys = origin_keys + (dest_keys or [])
    codes_all = pd.factorize(np.asarray(all_keys, dtype=object))[0]
    origin_codes = codes_all[:n]
    dest_codes = codes_all[n:] if dest_keys is not None else None

    # Event universe: every row is an OUTBOUND event of its origin account; rows with a distinct
    # destination are ALSO an INBOUND event of that account (a self-transfer contributes once,
    # as outbound — matching the online query that returns the row once, labeled outbound).
    row_ids = np.arange(n)
    if dest_codes is not None:
        inbound_mask = dest_codes != origin_codes
        event_account = np.concatenate([origin_codes, dest_codes[inbound_mask]])
        event_times = np.concatenate([seconds, seconds[inbound_mask]])
        event_rows = np.concatenate([row_ids, row_ids[inbound_mask]])
        event_amounts = np.concatenate([amounts, amounts[inbound_mask]])
        event_inbound = np.concatenate([np.zeros(n), np.ones(int(inbound_mask.sum()))])
        event_round = np.concatenate([is_round, is_round[inbound_mask]])
        event_country = np.concatenate([country_codes, country_codes[inbound_mask]])
        event_channel = np.concatenate([channel_codes, channel_codes[inbound_mask]])
    else:
        event_account = origin_codes
        event_times = seconds
        event_rows = row_ids
        event_amounts = amounts
        event_inbound = np.zeros(n)
        event_round = is_round
        event_country = country_codes
        event_channel = channel_codes
    stream = _build_stream(
        account_codes=event_account,
        times=event_times,
        row_ids=event_rows,
        amounts=event_amounts,
        inbound=event_inbound,
        is_round=event_round,
        country_codes=event_country,
        channel_codes=event_channel,
    )

    velocity = np.zeros(n)
    amount_sum_log = np.zeros(n)
    distinct_countries = np.zeros(n)
    inbound_velocity = np.zeros(n)
    inbound_amount_log = np.zeros(n)
    seconds_since_prev = np.full(n, _NO_PRIOR_SENTINEL_SECONDS)
    distinct_channels = np.zeros(n)
    round_share = np.zeros(n)
    dest_fan_in = np.zeros(n)
    dest_inbound_amount = amounts.copy()  # the current row is always an inbound to its dest.
    dest_outbound_velocity = np.zeros(n)
    dest_outbound_amount = np.zeros(n)

    rows_by_origin: dict[int, list[int]] = defaultdict(list)
    for index, code in enumerate(origin_codes):
        rows_by_origin[int(code)].append(index)
    for code, row_indices in rows_by_origin.items():
        rows = np.asarray(row_indices, dtype=int)
        lo, hi = _window_bounds(stream, code, seconds[rows], history_max)
        counts = (hi - lo).astype(float)
        velocity[rows] = counts
        window_amounts = stream.prefix_amount[hi] - stream.prefix_amount[lo]
        amount_sum_log[rows] = np.log1p(amounts[rows] + window_amounts)
        inbound_velocity[rows] = stream.prefix_inbound[hi] - stream.prefix_inbound[lo]
        inbound_amount_log[rows] = np.log1p(
            stream.prefix_inbound_amount[hi] - stream.prefix_inbound_amount[lo]
        )
        round_counts = stream.prefix_round[hi] - stream.prefix_round[lo]
        round_share[rows] = (is_round[rows] + round_counts) / (1.0 + counts)
        has_prior = hi > lo
        prev_index = np.maximum(hi - 1, 0)
        deltas = seconds[rows] - stream.times[prev_index]
        seconds_since_prev[rows] = np.where(has_prior, deltas, _NO_PRIOR_SENTINEL_SECONDS)
        for position, row in enumerate(rows):
            distinct_countries[row] = _distinct_in_window(
                stream.country, int(lo[position]), int(hi[position]), int(country_codes[row])
            )
            distinct_channels[row] = _distinct_in_window(
                stream.channel, int(lo[position]), int(hi[position]), int(channel_codes[row])
            )

    if dest_codes is not None:
        rows_by_dest: dict[int, list[int]] = defaultdict(list)
        for index, code in enumerate(dest_codes):
            rows_by_dest[int(code)].append(index)
        for code, row_indices in rows_by_dest.items():
            rows = np.asarray(row_indices, dtype=int)
            lo, hi = _window_bounds(stream, code, seconds[rows], history_max)
            window_counts = (hi - lo).astype(float)
            window_inbound = stream.prefix_inbound[hi] - stream.prefix_inbound[lo]
            window_amounts = stream.prefix_amount[hi] - stream.prefix_amount[lo]
            window_inbound_amounts = (
                stream.prefix_inbound_amount[hi] - stream.prefix_inbound_amount[lo]
            )
            dest_fan_in[rows] = window_inbound
            dest_inbound_amount[rows] = amounts[rows] + window_inbound_amounts
            dest_outbound_velocity[rows] = window_counts - window_inbound
            dest_outbound_amount[rows] = window_amounts - window_inbound_amounts

    columns: dict[str, np.ndarray] = {
        "amount_log": np.log1p(amounts),
        "hour_of_day": parsed.dt.hour.to_numpy(dtype=float),
        "day_of_week": parsed.dt.dayofweek.to_numpy(dtype=float),
        "is_round_amount": is_round,
        "country_risk": np.array([country_risk(token) for token in countries]),
        "channel_risk": np.array([channel_risk(token) for token in channels]),
        "velocity_24h": velocity,
        "amount_24h_sum_log": amount_sum_log,
        "distinct_countries_24h": distinct_countries,
        # IBM subject = sender; IEEE direction is absent, so the plan pins this same constant.
        "is_outbound": np.full(n, ibm_is_outbound(is_sender=True)),
        "inbound_velocity_24h": inbound_velocity,
        "inbound_amount_24h_log": inbound_amount_log,
        "seconds_since_prev_txn_log": np.log1p(seconds_since_prev),
        "distinct_channels_24h": distinct_channels,
        "round_amount_share_24h": round_share,
        "dest_fan_in_24h": dest_fan_in,
        "dest_inbound_amount_24h_log": np.log1p(dest_inbound_amount),
        "dest_outbound_velocity_24h": dest_outbound_velocity,
        "dest_outbound_amount_24h_log": np.log1p(dest_outbound_amount),
    }
    if tuple(columns) != FEATURE_NAMES or len(columns) != len(FEATURE_NAMES):
        raise AssertionError(f"feature columns {tuple(columns)} != FEATURE_NAMES {FEATURE_NAMES}")
    features = np.column_stack([columns[name] for name in FEATURE_NAMES])
    return features, labels


def split_chronological(
    features: np.ndarray, labels: np.ndarray, frame: pd.DataFrame, source: str
) -> DataSplit:
    """Split (X, y) chronologically (earliest→train, latest→holdout), keeping accounts whole.

    Differs from the synthetic path's seeded RANDOM split (lib.dataset.split_dataset): ordering
    accounts by their earliest transaction and assigning whole accounts to folds never scatters a
    laundering subgraph across folds and never random-samples rare patterns.
    """
    if source not in _REAL_SOURCES:
        raise _unsupported(source)
    keys = _origin_keys(frame, source)
    seconds = _occurred_seconds(frame, source)
    account_rows: dict[str, list[int]] = defaultdict(list)
    account_first: dict[str, float] = {}
    for index, key in enumerate(keys):
        account_rows[key].append(index)
        moment = float(seconds[index])
        account_first[key] = min(account_first.get(key, moment), moment)
    ordered_accounts = sorted(account_rows, key=lambda k: (account_first[k], k))

    n = features.shape[0]
    n_holdout = int(n * _HOLDOUT_FRACTION)
    n_calibration = int(n * _CALIBRATION_FRACTION)
    n_train_target = n - n_holdout - n_calibration
    train_idx: list[int] = []
    calibration_idx: list[int] = []
    holdout_idx: list[int] = []
    assigned = 0
    for account in ordered_accounts:  # earliest accounts fill train, latest fall to holdout
        if assigned < n_train_target:
            bucket = train_idx
        elif assigned < n_train_target + n_calibration:
            bucket = calibration_idx
        else:
            bucket = holdout_idx
        bucket.extend(account_rows[account])
        assigned += len(account_rows[account])
    train = np.array(train_idx, dtype=int)
    calibration = np.array(calibration_idx, dtype=int)
    holdout = np.array(holdout_idx, dtype=int)
    return DataSplit(
        x_train=features[train],
        y_train=labels[train],
        x_calibration=features[calibration],
        y_calibration=labels[calibration],
        x_holdout=features[holdout],
        y_holdout=labels[holdout],
    )


def source_columns(source: str) -> tuple[str, ...]:
    """Return the raw dataset columns consumed for `source` (the manifest's schema record)."""
    if source == IBM_AML:
        return _IBM_KEEP_COLUMNS
    if source == IEEE_CIS:
        return _IEEE_KEEP_COLUMNS
    raise _unsupported(source)


def sample_frame(frame: pd.DataFrame, source: str, n_rows: int, seed: int) -> pd.DataFrame:
    """Return a seeded, label-stratified subsample (~n_rows) of the frame for fast iteration.

    Sampling per class preserves the (rare) laundering base rate; a seeded numpy Generator makes
    it deterministic. Returns the frame unchanged when n_rows is not smaller than the frame.
    """
    if source == IBM_AML:
        labels = frame[_COL_IS_LAUNDERING].to_numpy()
    elif source == IEEE_CIS:
        labels = frame[_COL_IEEE_LABEL].to_numpy()
    else:
        raise _unsupported(source)
    if n_rows >= len(frame):
        return frame.reset_index(drop=True)
    if n_rows < 1:
        raise ValueError("sample rows must be at least 1")
    generator = np.random.default_rng(seed)
    fraction = n_rows / len(frame)
    keep: list[int] = []
    for value in np.unique(labels):
        positions = np.where(labels == value)[0]
        take = min(len(positions), max(1, round(len(positions) * fraction)))
        keep.extend(generator.choice(positions, size=take, replace=False).tolist())
    keep.sort()
    return frame.iloc[keep].reset_index(drop=True)


def demo_agency_index(bank: str, agency_count: int = _DEFAULT_DEMO_AGENCIES) -> int:
    """Map a source bank to one of `agency_count` demo agencies (deterministic tenancy spread).

    The demo-ingest path binds this partition to real Agency rows so ingested real data exercises
    multi-tenant isolation; the TRAINING matrix stays agency-agnostic (global training, ADR-015).
    """
    if agency_count < 1:
        raise ValueError("agency_count must be >= 1")
    digest = sum(bank.strip().encode("utf-8"))
    return digest % agency_count


def _required_row_text(row: Mapping[str, Any], column: str) -> str:
    """Return a non-empty IBM column without echoing its value in validation failures."""
    value = row.get(column)
    if value is None or str(value).strip() == "":
        raise SchemaValidationError(column, "required")
    return str(value).strip()


def _ibm_timestamp(value: str) -> pd.Timestamp:
    """Parse one IBM timestamp as UTC, raising a value-free schema error on failure."""
    try:
        parsed = pd.to_datetime(value, utc=True)
    except (TypeError, ValueError) as exc:
        raise SchemaValidationError(_COL_TIMESTAMP, "invalid_datetime") from exc
    if pd.isna(parsed):
        raise SchemaValidationError(_COL_TIMESTAMP, "invalid_datetime")
    return pd.Timestamp(parsed)


def map_ibm_demo_row(
    row: Mapping[str, Any], row_index: int, agency_count: int = _DEFAULT_DEMO_AGENCIES
) -> IbmDemoTransaction:
    """Map one public IBM row into the canonical masked-ingest path and a tenant partition."""
    from_bank = _required_row_text(row, _COL_FROM_BANK)
    from_account = _required_row_text(row, _COL_FROM_ACCOUNT)
    to_bank = _required_row_text(row, _COL_TO_BANK)
    to_account = _required_row_text(row, _COL_TO_ACCOUNT)
    timestamp = _required_row_text(row, _COL_TIMESTAMP)
    amount = _required_row_text(row, _COL_AMOUNT_PAID)
    payment_currency = _required_row_text(row, _COL_PAYMENT_CURRENCY)
    payment_format = _required_row_text(row, _COL_PAYMENT_FORMAT)
    digest_input = "\x1f".join(
        (str(row_index), timestamp, from_bank, from_account, to_bank, to_account, amount)
    )
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
    canonical = build_canonical(
        external_id=f"{_DEMO_EXTERNAL_ID_PREFIX}-{digest[:_DEMO_EXTERNAL_ID_DIGEST_LENGTH]}",
        amount=amount,
        currency=ibm_currency(payment_currency),
        occurred_at=_ibm_timestamp(timestamp).to_pydatetime(),
        origin_account=ibm_account_key(from_bank, from_account),
        dest_account=ibm_account_key(to_bank, to_account),
        channel=ibm_channel(payment_format),
        country=ibm_country(payment_currency),
        features={"dataset_source": IBM_AML},
    )
    return IbmDemoTransaction(
        agency_index=demo_agency_index(from_bank, agency_count),
        canonical=canonical,
    )


@dataclass(frozen=True)
class _AnchorSpec:
    """One laundering anchor account: its tenant and the time neighborhood it claims."""

    tenant: int
    window_start: float
    window_end: float


def _ibm_chunks(csv_path: Path) -> Iterator[pd.DataFrame]:
    """Yield string-typed chunks of the IBM CSV (memory-safe scan of the full file)."""
    yield from pd.read_csv(
        csv_path,
        usecols=list(_IBM_KEEP_COLUMNS),
        dtype=str,
        chunksize=_CASE_PACK_CHUNK_ROWS,
    )


def _epoch_seconds(value: str) -> float:
    """Parse one IBM timestamp string to float epoch seconds (UTC)."""
    return float(_ibm_timestamp(value).timestamp())


def _select_anchors(
    csv_path: Path,
    anchor_budget: int,
    agency_count: int,
    tenant_weights: tuple[int, ...] = _DEFAULT_TENANT_WEIGHTS,
) -> dict[str, _AnchorSpec]:
    """Pick the earliest distinct laundering origin accounts and assign their tenants.

    Deterministic: laundering rows are ordered by (timestamp, file position); `tenant_weights`
    cycles tenants so the primary (batch-scored) tenant receives the caller's chosen share of
    laundering neighborhoods (the default is the study's 60/20/20 spread).
    """
    laundering: list[tuple[float, int, str]] = []
    for chunk in _ibm_chunks(csv_path):
        hits = chunk[chunk[_COL_IS_LAUNDERING] == _LAUNDERING_LABEL]
        # read_csv(chunksize=...) keeps one global RangeIndex across chunks, so `position`
        # is already the file-order row index (the deterministic tie-break).
        for position, row in zip(hits.index, hits.to_dict(orient="records"), strict=True):
            key = ibm_account_key(str(row[_COL_FROM_BANK]), str(row[_COL_FROM_ACCOUNT]))
            laundering.append((_epoch_seconds(str(row[_COL_TIMESTAMP])), int(position), key))
    if not laundering:
        raise ValueError(
            "case pack found no laundering ground truth in the dataset file — "
            "verify the fetched variant is HI-Small_Trans.csv"
        )
    if not tenant_weights:
        raise ValueError("tenant_weights must contain at least one partition index")
    laundering.sort(key=lambda item: (item[0], item[1], item[2]))
    anchors: dict[str, _AnchorSpec] = {}
    for moment, _, key in laundering:
        if key in anchors:
            continue
        tenant = tenant_weights[len(anchors) % len(tenant_weights)] % agency_count
        anchors[key] = _AnchorSpec(
            tenant=tenant,
            window_start=moment - _NEIGHBORHOOD_WINDOW_SECONDS,
            window_end=moment + _NEIGHBORHOOD_WINDOW_SECONDS,
        )
        if len(anchors) >= anchor_budget:
            break
    return anchors


def load_ibm_case_pack(
    paths: DatasetPaths,
    *,
    rows: int,
    agency_count: int = _DEFAULT_DEMO_AGENCIES,
    tenant_weights: tuple[int, ...] = _DEFAULT_TENANT_WEIGHTS,
) -> list[IbmDemoTransaction]:
    """Build the deterministic demo case pack: laundering neighborhoods + benign controls.

    Replaces the old CSV prefix (which contained zero laundering context). Selection is a pure
    function of the file + parameters: anchor accounts are the earliest distinct laundering
    senders; each contributes its complete account/time neighborhood (rows where it is sender OR
    receiver inside ±3 days of its first laundering row, capped); benign stride-sampled controls
    (never touching an anchor) fill the remaining budget. A neighborhood stays inside ONE tenant
    so the served history windows match training. The public label steers selection only — it is
    never persisted, logged, or converted into an alert.
    """
    if rows < 1:
        raise ValueError("case pack rows must be at least 1")
    csv_path = Path(paths.directory) / paths.files[0].name
    total_rows = paths.files[0].row_count
    anchor_budget = max(_ANCHOR_MIN, rows // _ANCHOR_ROW_DIVISOR)
    anchors = _select_anchors(csv_path, anchor_budget, agency_count, tenant_weights)

    stride = max(1, total_rows // max(1, rows))
    benign_cap = rows * _BENIGN_OVERSAMPLE
    neighborhoods: dict[str, list[tuple[int, dict[str, Any]]]] = {key: [] for key in anchors}
    benign: list[tuple[int, dict[str, Any]]] = []
    offset = 0
    for chunk in _ibm_chunks(csv_path):
        records = chunk.to_dict(orient="records")
        for position, row in enumerate(records):
            row_index = offset + position
            okey = ibm_account_key(str(row[_COL_FROM_BANK]), str(row[_COL_FROM_ACCOUNT]))
            dkey = ibm_account_key(str(row[_COL_TO_BANK]), str(row[_COL_TO_ACCOUNT]))
            anchor_key = okey if okey in anchors else (dkey if dkey in anchors else None)
            if anchor_key is not None:
                bucket = neighborhoods[anchor_key]
                if len(bucket) < _NEIGHBORHOOD_MAX_ROWS:
                    spec = anchors[anchor_key]
                    moment = _epoch_seconds(str(row[_COL_TIMESTAMP]))
                    is_laundering = str(row[_COL_IS_LAUNDERING]) == _LAUNDERING_LABEL
                    # Laundering rows of the anchor always belong; context rows must fall
                    # inside the anchor's time neighborhood.
                    if is_laundering or spec.window_start <= moment <= spec.window_end:
                        bucket.append((row_index, row))
                continue
            if (
                str(row[_COL_IS_LAUNDERING]) == _BENIGN_LABEL
                and row_index % stride == 0
                and len(benign) < benign_cap
            ):
                benign.append((row_index, row))
        offset += len(records)

    pack: list[tuple[int, dict[str, Any], int]] = []
    used: set[int] = set()
    for key, spec in anchors.items():
        bucket = neighborhoods[key]
        if not bucket:
            continue
        if pack and len(pack) + len(bucket) > rows:
            break  # keep whole neighborhoods; benign controls fill the remainder.
        for row_index, row in bucket:
            if row_index in used or len(pack) >= rows:
                continue
            used.add(row_index)
            pack.append((row_index, row, spec.tenant))
    for row_index, row in benign:
        if len(pack) >= rows:
            break
        if row_index in used:
            continue
        used.add(row_index)
        tenant = demo_agency_index(str(row[_COL_FROM_BANK]), agency_count)
        pack.append((row_index, row, tenant))

    pack.sort(key=lambda item: item[0])
    transactions: list[IbmDemoTransaction] = []
    skipped = 0
    for row_index, row, tenant in pack:
        try:
            mapped = map_ibm_demo_row(row, row_index, agency_count)
        except SchemaValidationError:
            # Un-ingestable source row (e.g. sub-cent dust that rounds to zero cents) — the
            # canonical boundary would reject it, so the pack skips it deterministically.
            skipped += 1
            continue
        transactions.append(IbmDemoTransaction(agency_index=tenant, canonical=mapped.canonical))
    if skipped:
        print(f">> case pack: skipped {skipped} un-ingestable source rows (canonical rejects)")
    return transactions
