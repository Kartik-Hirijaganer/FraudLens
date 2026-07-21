"""Real AML loader tests (real-AML training plan Phase 3 + full-IBM plan Phases 4/5, the
**anti-skew guarantee**). Runs against the committed canonical-column sample
`data/aml_train_sample.csv` plus dense in-memory frames (no download). Asserts
`build_feature_matrix` reproduces the live scorer's `extract_features` column-for-column —
including the v2 direction-split / counterparty windows and the online most-recent-rows cap —
the strict 24h window boundary, the Decimal cent-precision round-amount check, that the
chronological split keeps an account's rows inside one fold, determinism, and that the demo
case pack is deterministic, label-hygienic, and neighborhood-complete."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fetch_dataset import DatasetFile, DatasetPaths
from fraudlens_core import RuleContext
from fraudlens_core.rules.base import RuleTransaction, TransactionDirection
from fraudlens_ml.scoring.features import FEATURE_NAMES, extract_feature_vector
from lib.aml_fraud import (
    IBM_AML,
    IEEE_CIS,
    build_feature_matrix,
    demo_agency_index,
    load_frame,
    load_ibm_case_pack,
    map_ibm_demo_row,
    sample_frame,
    servable_frame,
    split_chronological,
)
from lib.aml_mapping import ibm_account_key, ibm_channel, ibm_country, ibm_currency

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_SAMPLE = "aml_train_sample.csv"
_WINDOW_HOURS = 168
_DEFAULT_CAP = 100


def _paths() -> DatasetPaths:
    """A DatasetPaths pointing at the committed sample fixture (no network)."""
    return DatasetPaths(
        source=IBM_AML,
        directory=str(_DATA_DIR),
        files=[DatasetFile(name=_SAMPLE, sha256="0" * 64, row_count=12)],
    )


def _frame() -> pd.DataFrame:
    return load_frame(_paths(), IBM_AML)


def _ieee_frame() -> pd.DataFrame:
    """A PHI-free IEEE-shaped frame with repeated cards and both labels."""
    return pd.DataFrame(
        {
            "TransactionDT": [0, 3_600, 90_000, 180_000, 270_000, 360_000],
            "TransactionAmt": [100, 59.5, 200, 25, 300, 75],
            "ProductCD": ["W", "H", "R", "S", "C", "W"],
            "card1": ["CARD-A", "CARD-A", "CARD-B", "CARD-C", "CARD-D", "CARD-E"],
            "addr2": ["87", "87", "87", "87", "87", "87"],
            "isFraud": [0, 1, 0, 1, 0, 1],
        }
    )


def _dense_frame() -> pd.DataFrame:
    """A deterministic dense frame with dual-role accounts, ties, self-transfers, hot windows."""
    rng = np.random.default_rng(11)
    base = pd.Timestamp("2022-09-01 00:00", tz="UTC")
    rows = []
    for i in range(240):
        from_bank, from_account = ("10", "HOT") if i % 2 == 0 else ("20", f"B{i % 3}")
        to_bank, to_account = ("10", "HOT") if i % 7 == 0 else ("30", f"C{i % 3}")
        if i % 29 == 0:  # self-transfer: origin key == dest key
            to_bank, to_account = from_bank, from_account
        minutes = int(rng.integers(0, 60 * 48))
        stamp = base + pd.Timedelta(minutes=minutes - (minutes % 5 if i % 4 == 0 else 0))
        amount = float(rng.choice([100.00, 250.50, 9000.00, 42.42]))
        rows.append(
            {
                "Timestamp": stamp.strftime("%Y/%m/%d %H:%M"),
                "From Bank": from_bank,
                "Account": from_account,
                "To Bank": to_bank,
                "Account.1": to_account,
                "Amount Paid": f"{amount:.2f}",
                "Payment Currency": str(rng.choice(["US Dollar", "Euro", "Bitcoin"])),
                "Payment Format": str(rng.choice(["ACH", "Wire", "Cash", "Credit Card"])),
                "Is Laundering": str(int(rng.random() < 0.05)),
            }
        )
    return pd.DataFrame(rows, dtype=str)


def _rule_transaction(row: pd.Series, direction: TransactionDirection) -> RuleTransaction:
    """Project one raw IBM row onto the PHI-free analytical view the scorer consumes."""
    return RuleTransaction(
        amount=Decimal(str(row["Amount Paid"])),
        currency=ibm_currency(row["Payment Currency"]),
        country=ibm_country(row["Payment Currency"]),
        channel=ibm_channel(row["Payment Format"]),
        occurred_at=pd.to_datetime(row["Timestamp"], utc=True).to_pydatetime(),
        direction=direction,
    )


def _account_history(
    frame: pd.DataFrame, account: str, now: datetime, cap: int
) -> tuple[RuleTransaction, ...]:
    """Rebuild the online `same_account_history` result: both roles, windowed, capped.

    Mirrors the SQL: rows touching the account as origin OR destination, strictly before `now`
    within the lookback window, most-recent `cap` kept (ties broken toward later file order —
    the deterministic tie-break serving now also applies).
    """
    times = pd.to_datetime(frame["Timestamp"], utc=True)
    okeys = [
        ibm_account_key(str(b), str(a))
        for b, a in zip(frame["From Bank"], frame["Account"], strict=True)
    ]
    dkeys = [
        ibm_account_key(str(b), str(a))
        for b, a in zip(frame["To Bank"], frame["Account.1"], strict=True)
    ]
    floor = now - timedelta(hours=_WINDOW_HOURS)
    events: list[tuple[pd.Timestamp, int, TransactionDirection]] = []
    for j in range(len(frame)):
        moment = times.iloc[j]
        if moment >= now or moment < floor:
            continue
        if okeys[j] == account:
            events.append((moment, j, TransactionDirection.OUTBOUND))
        elif dkeys[j] == account:
            events.append((moment, j, TransactionDirection.INBOUND))
    events.sort(key=lambda item: (item[0], item[1]))
    return tuple(_rule_transaction(frame.iloc[j], direction) for _, j, direction in events[-cap:])


def _context_for_row(frame: pd.DataFrame, index: int, cap: int = _DEFAULT_CAP) -> RuleContext:
    """Rebuild the live RuleContext for one row (origin + counterparty windows, capped)."""
    row = frame.iloc[index]
    now = pd.to_datetime(row["Timestamp"], utc=True).to_pydatetime()
    origin = ibm_account_key(str(row["From Bank"]), str(row["Account"]))
    dest = ibm_account_key(str(row["To Bank"]), str(row["Account.1"]))
    return RuleContext(
        transaction=_rule_transaction(row, TransactionDirection.OUTBOUND),
        history=_account_history(frame, origin, now, cap),
        counterparty_history=_account_history(frame, dest, now, cap),
    )


def test_build_feature_matrix_matches_extract_features_column_for_column() -> None:
    frame = _frame()
    features, labels = build_feature_matrix(frame, IBM_AML)
    assert features.shape == (len(frame), len(FEATURE_NAMES))
    assert labels.tolist() == [int(v) for v in frame["Is Laundering"]]
    for index in range(len(frame)):
        expected = extract_feature_vector(_context_for_row(frame, index)).flatten()
        np.testing.assert_allclose(
            features[index], expected, atol=1e-9, err_msg=f"row {index} diverges from scorer"
        )


def test_dense_dual_role_frame_matches_scorer_including_v2_features() -> None:
    """The anti-skew guarantee under fan-in, self-transfers, ties, and inbound windows."""
    frame = _dense_frame()
    features, _ = build_feature_matrix(frame, IBM_AML)
    fan_in = features[:, FEATURE_NAMES.index("dest_fan_in_24h")]
    inbound = features[:, FEATURE_NAMES.index("inbound_velocity_24h")]
    assert fan_in.max() > 0  # the counterparty window is genuinely exercised
    assert inbound.max() > 0  # direction-split origin window is genuinely exercised
    for index in range(len(frame)):
        expected = extract_feature_vector(_context_for_row(frame, index)).flatten()
        np.testing.assert_allclose(
            features[index], expected, atol=1e-9, err_msg=f"row {index} diverges from scorer"
        )


def test_history_cap_mirrors_online_most_recent_limit() -> None:
    """With a tiny cap the builder must agree with a capped online history, row for row."""
    frame = _dense_frame()
    cap = 5
    features, _ = build_feature_matrix(frame, IBM_AML, history_max=cap)
    velocity = features[:, FEATURE_NAMES.index("velocity_24h")]
    assert velocity.max() == cap  # the cap genuinely binds on the hot account
    for index in range(len(frame)):
        expected = extract_feature_vector(_context_for_row(frame, index, cap=cap)).flatten()
        np.testing.assert_allclose(
            features[index], expected, atol=1e-9, err_msg=f"row {index} diverges under the cap"
        )


def test_24h_window_boundary_excludes_current_includes_priors() -> None:
    frame = _frame()
    features, _ = build_feature_matrix(frame, IBM_AML)
    velocity = features[:, FEATURE_NAMES.index("velocity_24h")]
    # Account AAA111 rows (chronological): 09/01 08:00, 09/01 20:00, 09/02 09:00, 09/05 10:00.
    assert velocity[0] == 0.0  # first txn: no priors
    assert velocity[1] == 1.0  # 12h later: the first txn is in-window
    assert velocity[2] == 1.0  # 25h after first (out) but 13h after second (in) -> 1
    assert velocity[3] == 0.0  # days later: window empty


def test_is_round_amount_uses_decimal_cent_precision() -> None:
    frame = _frame()
    features, _ = build_feature_matrix(frame, IBM_AML)
    is_round = features[:, FEATURE_NAMES.index("is_round_amount")]
    assert is_round[0] == 1.0  # 9000.00 -> multiple of 100
    assert is_round[2] == 0.0  # 59.50  -> not a multiple of 100


def test_tie_timestamps_do_not_count_each_other() -> None:
    frame = _frame()
    features, _ = build_feature_matrix(frame, IBM_AML)
    velocity = features[:, FEATURE_NAMES.index("velocity_24h")]
    # BBB222 has two txns at the SAME timestamp -> neither is a prior of the other (strict `< t`).
    assert velocity[4] == 0.0
    assert velocity[5] == 0.0


def test_burstiness_sentinel_and_prev_txn_seconds() -> None:
    frame = _frame()
    features, _ = build_feature_matrix(frame, IBM_AML)
    seconds_log = features[:, FEATURE_NAMES.index("seconds_since_prev_txn_log")]
    sentinel = float(np.log1p(86_400.0))
    assert seconds_log[0] == pytest.approx(sentinel)  # no prior in window
    assert seconds_log[1] == pytest.approx(float(np.log1p(12 * 3600)))  # 12h after the first


def test_split_chronological_keeps_accounts_whole_and_is_deterministic() -> None:
    frame = _frame()
    features, labels = build_feature_matrix(frame, IBM_AML)
    split = split_chronological(features, labels, frame, IBM_AML)
    total = split.x_train.shape[0] + split.x_calibration.shape[0] + split.x_holdout.shape[0]
    assert total == len(frame)
    # Every account's rows land in exactly one fold (rows are identified by their feature vector,
    # which is unique per row in this fixture) — reconstruct fold membership by origin account.
    keys = [
        ibm_account_key(str(b), str(a))
        for b, a in zip(frame["From Bank"], frame["Account"], strict=True)
    ]
    fold_of: dict[str, set[str]] = {}
    for name, fold in (("t", split.x_train), ("c", split.x_calibration), ("h", split.x_holdout)):
        for vector in fold:
            row = int(np.where((features == vector).all(axis=1))[0][0])
            fold_of.setdefault(keys[row], set()).add(name)
    assert all(len(folds) == 1 for folds in fold_of.values())
    # Determinism: same inputs -> identical fold sizes.
    again = split_chronological(features, labels, frame, IBM_AML)
    assert (again.x_train.shape, again.x_calibration.shape, again.x_holdout.shape) == (
        split.x_train.shape,
        split.x_calibration.shape,
        split.x_holdout.shape,
    )


def test_build_feature_matrix_is_deterministic() -> None:
    frame = _frame()
    first, _ = build_feature_matrix(frame, IBM_AML)
    second, _ = build_feature_matrix(frame, IBM_AML)
    np.testing.assert_array_equal(first, second)


def test_ieee_feature_matrix_and_split_use_the_shared_source_contract() -> None:
    frame = _ieee_frame()
    features, labels = build_feature_matrix(frame, IEEE_CIS)
    assert features.shape == (len(frame), len(FEATURE_NAMES))
    assert labels.tolist() == frame["isFraud"].tolist()
    assert features[0, FEATURE_NAMES.index("channel_risk")] > 0
    assert features[1, FEATURE_NAMES.index("velocity_24h")] == 1.0
    assert np.all(features[:, FEATURE_NAMES.index("is_outbound")] == 1.0)
    # A card-stream source has no counterparty side: fan-in stays 0, dest sum = current amount.
    assert np.all(features[:, FEATURE_NAMES.index("dest_fan_in_24h")] == 0.0)
    np.testing.assert_allclose(
        features[:, FEATURE_NAMES.index("dest_inbound_amount_24h_log")],
        features[:, FEATURE_NAMES.index("amount_log")],
        atol=1e-9,
    )
    split = split_chronological(features, labels, frame, IEEE_CIS)
    assert sum(
        fold.shape[0] for fold in (split.x_train, split.x_calibration, split.x_holdout)
    ) == len(frame)


def test_ieee_sampling_is_seeded_and_label_stratified() -> None:
    frame = _ieee_frame()
    first = sample_frame(frame, IEEE_CIS, 4, seed=1729)
    second = sample_frame(frame, IEEE_CIS, 4, seed=1729)
    pd.testing.assert_frame_equal(first, second)
    assert set(first["isFraud"]) == {0, 1}


def test_demo_agency_index_is_deterministic_and_bounded() -> None:
    assert demo_agency_index("10") == demo_agency_index(" 10 ")  # normalized
    assert all(0 <= demo_agency_index(str(bank), 3) < 3 for bank in range(100))


def test_map_ibm_demo_row_builds_canonical_partition_without_persisting_source_label() -> None:
    row = _frame().iloc[0].to_dict()
    mapped = map_ibm_demo_row(row, 0)
    assert mapped.agency_index == demo_agency_index(str(row["From Bank"]))
    assert mapped.canonical.external_id.startswith("IBM-AML-")
    assert mapped.canonical.currency == "USD"
    assert mapped.canonical.features == {"dataset_source": IBM_AML}
    assert "Is Laundering" not in mapped.canonical.features


def test_case_pack_is_deterministic_and_neighborhood_complete() -> None:
    first = load_ibm_case_pack(_paths(), rows=10)
    second = load_ibm_case_pack(_paths(), rows=10)
    assert first == second
    assert 0 < len(first) <= 10
    ids = {item.canonical.external_id for item in first}
    assert len(ids) == len(first)  # no duplicate rows across tenants
    # Every laundering ANCHOR's neighborhood arrives whole and inside ONE tenant: AAA111's
    # three in-window rows must all be present with one agency index.
    aaa_rows = [
        item for item in first if item.canonical.origin_account == ibm_account_key("10", "AAA111")
    ]
    assert len(aaa_rows) == 3
    assert len({item.agency_index for item in aaa_rows}) == 1
    # Benign controls exist alongside the laundering neighborhoods.
    origins = {item.canonical.origin_account for item in first}
    assert ibm_account_key("30", "CCC333") in origins or ibm_account_key("50", "EEE555") in origins


def test_case_pack_never_persists_the_source_label() -> None:
    pack = load_ibm_case_pack(_paths(), rows=10)
    for item in pack:
        assert item.canonical.features == {"dataset_source": IBM_AML}


def test_case_pack_respects_the_row_budget_with_tiny_budgets() -> None:
    pack = load_ibm_case_pack(_paths(), rows=2)
    assert 0 < len(pack) <= 2


def test_case_pack_without_laundering_ground_truth_raises(tmp_path: Path) -> None:
    frame = _frame().copy()
    frame["Is Laundering"] = "0"
    target = tmp_path / _SAMPLE
    frame.to_csv(target, index=False)
    paths = DatasetPaths(
        source=IBM_AML,
        directory=str(tmp_path),
        files=[DatasetFile(name=_SAMPLE, sha256="0" * 64, row_count=len(frame))],
    )
    with pytest.raises(ValueError, match="no laundering ground truth"):
        load_ibm_case_pack(paths, rows=10)


def test_unsupported_source_raises() -> None:
    frame = _frame()
    for call in (
        lambda: load_frame(_paths(), "unknown"),
        lambda: build_feature_matrix(frame, "synthetic"),
        lambda: split_chronological(*build_feature_matrix(frame, IBM_AML), frame, "synthetic"),
        lambda: sample_frame(frame, "unknown", 3, seed=1729),
    ):
        try:
            call()
        except ValueError:
            continue
        raise AssertionError("expected ValueError for unsupported source")


def test_servable_frame_drops_rows_the_ingest_boundary_would_reject() -> None:
    frame = _frame().copy()
    frame.loc[0, "Amount Paid"] = "0.004"  # sub-half-cent dust -> rounds to zero cents
    frame.loc[1, "Amount Paid"] = "0.005"  # rounds to a servable 0.01
    kept = servable_frame(frame, IBM_AML)
    assert len(kept) == len(frame) - 1
    assert "0.004" not in set(kept["Amount Paid"])
    features, _ = build_feature_matrix(kept, IBM_AML)
    assert features.shape[0] == len(kept)


def test_case_pack_skips_un_ingestable_dust_rows(tmp_path: Path) -> None:
    frame = _frame().copy()
    frame.loc[3, "Amount Paid"] = "0.004"  # inside the AAA111 neighborhood window? row 3 is benign
    frame.loc[1, "Amount Paid"] = "0.002"  # in-window AAA111 context row becomes dust
    target = tmp_path / _SAMPLE
    frame.to_csv(target, index=False)
    paths = DatasetPaths(
        source=IBM_AML,
        directory=str(tmp_path),
        files=[DatasetFile(name=_SAMPLE, sha256="0" * 64, row_count=len(frame))],
    )
    pack = load_ibm_case_pack(paths, rows=10)
    amounts = {item.canonical.amount for item in pack}
    assert all(amount > 0 for amount in amounts)
    ids = {item.canonical.external_id for item in pack}
    assert len(ids) == len(pack)
