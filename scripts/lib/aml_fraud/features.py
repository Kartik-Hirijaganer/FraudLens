"""Summary: Train/serve-parity feature construction and chronological splitting for AML data.

Key classes:
- (none)

Key functions:
- build_feature_matrix: reproduce the live FEATURE_NAMES-ordered scorer inputs.
- split_chronological: create account-whole chronological train/calibration/holdout folds.

Notes:
- Windows use strict [t-24h, t) semantics and the online most-recent history cap.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from fraudlens_ml.scoring.features import FEATURE_NAMES, channel_risk, country_risk
from lib.aml_mapping import (
    ibm_is_outbound,
)
from lib.dataset import _CALIBRATION_FRACTION, _HOLDOUT_FRACTION, DataSplit

if TYPE_CHECKING:
    pass

from lib.aml_fraud.frames import (
    _AMOUNT_QUANTUM,
    _DEFAULT_HISTORY_MAX,
    _NO_PRIOR_SENTINEL_SECONDS,
    _REAL_SOURCES,
    _ROUND_AMOUNT_MODULUS,
    _build_stream,
    _dest_keys,
    _distinct_in_window,
    _occurred_seconds,
    _origin_keys,
    _source_values,
    _timestamps,
    _unsupported,
    _window_bounds,
)


def build_feature_matrix(
    frame: pd.DataFrame, source: str, *, history_max: int = _DEFAULT_HISTORY_MAX
) -> tuple[np.ndarray, np.ndarray]:
    """Map the frame to (X in FEATURE_NAMES order, y), replicating extract_features exactly.

    `history_max` mirrors `settings.investigation_history_max` — the online cap on history rows
    per window — so offline features equal what the live extractor is actually fed.
    """
    amounts_decimal, countries, channels, labels = _source_values(frame, source)
    amount_cents = np.fromiter(
        (int(value / _AMOUNT_QUANTUM) for value in amounts_decimal),
        dtype=np.int64,
        count=len(amounts_decimal),
    )
    amounts = amount_cents.astype(np.float64) * float(_AMOUNT_QUANTUM)
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
        event_amount_cents = np.concatenate([amount_cents, amount_cents[inbound_mask]])
        event_inbound = np.concatenate([np.zeros(n), np.ones(int(inbound_mask.sum()))])
        event_round = np.concatenate([is_round, is_round[inbound_mask]])
        event_country = np.concatenate([country_codes, country_codes[inbound_mask]])
        event_channel = np.concatenate([channel_codes, channel_codes[inbound_mask]])
    else:
        event_account = origin_codes
        event_times = seconds
        event_rows = row_ids
        event_amount_cents = amount_cents
        event_inbound = np.zeros(n)
        event_round = is_round
        event_country = country_codes
        event_channel = channel_codes
    stream = _build_stream(
        account_codes=event_account,
        times=event_times,
        row_ids=event_rows,
        amount_cents=event_amount_cents,
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
        window_amounts = (stream.prefix_amount[hi] - stream.prefix_amount[lo]).astype(
            np.float64
        ) * float(_AMOUNT_QUANTUM)
        amount_sum_log[rows] = np.log1p(amounts[rows] + window_amounts)
        inbound_velocity[rows] = stream.prefix_inbound[hi] - stream.prefix_inbound[lo]
        inbound_amounts = (
            stream.prefix_inbound_amount[hi] - stream.prefix_inbound_amount[lo]
        ).astype(np.float64) * float(_AMOUNT_QUANTUM)
        # Prefix subtraction can leave sub-microcent noise for an empty directional slice.
        # The live scorer sums an empty tuple to exact zero, so pin the same semantic result.
        inbound_amounts = np.where(inbound_velocity[rows] == 0, 0.0, inbound_amounts)
        inbound_amount_log[rows] = np.log1p(inbound_amounts)
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
            window_amounts = (stream.prefix_amount[hi] - stream.prefix_amount[lo]).astype(
                np.float64
            ) * float(_AMOUNT_QUANTUM)
            window_inbound_amounts = (
                stream.prefix_inbound_amount[hi] - stream.prefix_inbound_amount[lo]
            ).astype(np.float64) * float(_AMOUNT_QUANTUM)
            dest_fan_in[rows] = window_inbound
            dest_inbound_amount[rows] = amounts[rows] + window_inbound_amounts
            dest_outbound_velocity[rows] = window_counts - window_inbound
            outbound_amounts = window_amounts - window_inbound_amounts
            # Match extract_features' exact sum(empty)==0 despite prefix cancellation noise.
            dest_outbound_amount[rows] = np.where(
                dest_outbound_velocity[rows] == 0, 0.0, outbound_amounts
            )

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
