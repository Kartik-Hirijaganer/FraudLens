"""Phase 5 feature-extraction tests (plan §16 Phase 5: "feature determinism"). Verify the
PHI-free RuleContext->vector mapping is deterministic, ordered, and computes each feature
(risk lookups + defaults, the 24h velocity/amount/country window, round-amount, direction)."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import numpy as np
import pytest

from fraudlens_core import RuleContext
from fraudlens_core.rules.base import RuleTransaction, TransactionDirection
from fraudlens_ml.scoring import current_feature_spec, extract_feature_vector, extract_features
from fraudlens_ml.scoring.features import (
    FEATURE_NAMES,
    feature_vector,
)

CtxFactory = Callable[..., RuleContext]


def test_feature_spec_lists_all_feature_names() -> None:
    spec = current_feature_spec()
    assert spec.version == 2
    assert tuple(spec.features) == FEATURE_NAMES
    assert len(FEATURE_NAMES) == 19


def test_extract_features_is_deterministic(make_rule_context: CtxFactory) -> None:
    ctx = make_rule_context(amount="9500.00", country="NG", channel="wire")
    assert extract_features(ctx) == extract_features(ctx)
    assert set(extract_features(ctx)) == set(FEATURE_NAMES)


def test_extract_features_computes_expected_values(make_rule_context: CtxFactory) -> None:
    ctx = make_rule_context(
        amount="5000.00",
        country="NG",
        channel="wire",
        occurred_at=datetime(2024, 6, 5, 3, 0, tzinfo=UTC),  # Wednesday, 3am
        direction=TransactionDirection.OUTBOUND,
    )
    features = extract_features(ctx)
    assert features["amount_log"] == pytest.approx(math.log1p(5000.0))
    assert features["hour_of_day"] == 3.0
    assert features["day_of_week"] == 2.0  # Wednesday
    assert features["is_round_amount"] == 1.0  # 5000 % 100 == 0
    assert features["country_risk"] == pytest.approx(0.85)  # NG high-risk
    assert features["channel_risk"] == pytest.approx(0.60)  # wire
    assert features["is_outbound"] == 1.0
    assert features["velocity_24h"] == 0.0
    assert features["distinct_countries_24h"] == 1.0  # just the current country


def test_unknown_country_and_channel_fall_back_to_defaults(make_rule_context: CtxFactory) -> None:
    features = extract_features(make_rule_context(country="ZZ", channel="carrier-pigeon"))
    assert features["country_risk"] == pytest.approx(0.15)  # default
    assert features["channel_risk"] == pytest.approx(0.30)  # default


def test_inbound_and_non_round_amount(make_rule_context: CtxFactory) -> None:
    features = extract_features(
        make_rule_context(amount="123.45", direction=TransactionDirection.INBOUND)
    )
    assert features["is_outbound"] == 0.0
    assert features["is_round_amount"] == 0.0


def test_velocity_window_counts_only_recent_same_account_history(
    make_rule_context: CtxFactory,
) -> None:
    now = datetime(2024, 6, 5, 12, 0, tzinfo=UTC)
    recent = RuleTransaction(
        amount=Decimal("200.00"),
        currency="USD",
        country="GB",
        channel="wire",
        occurred_at=datetime(2024, 6, 5, 6, 0, tzinfo=UTC),  # 6h before -> in window
    )
    stale = RuleTransaction(
        amount=Decimal("999.00"),
        currency="USD",
        country="FR",
        channel="wire",
        occurred_at=datetime(2024, 6, 3, 6, 0, tzinfo=UTC),  # >24h before -> excluded
    )
    features = extract_features(
        make_rule_context(amount="100.00", occurred_at=now, history=(recent, stale))
    )
    assert features["velocity_24h"] == 1.0  # only `recent`
    assert features["distinct_countries_24h"] == 2.0  # US (current) + GB (recent)
    assert features["amount_24h_sum_log"] == pytest.approx(math.log1p(100.0 + 200.0))


def test_v2_direction_split_burstiness_and_structuring_share(
    make_rule_context: CtxFactory,
) -> None:
    now = datetime(2024, 6, 5, 12, 0, tzinfo=UTC)
    inbound_prior = RuleTransaction(
        amount=Decimal("400.00"),
        currency="USD",
        country="GB",
        channel="ach",
        occurred_at=datetime(2024, 6, 5, 9, 0, tzinfo=UTC),  # 3h before, INTO the account
        direction=TransactionDirection.INBOUND,
    )
    outbound_prior = RuleTransaction(
        amount=Decimal("123.45"),
        currency="USD",
        country="US",
        channel="wire",
        occurred_at=datetime(2024, 6, 5, 10, 0, tzinfo=UTC),  # 2h before, most recent prior
        direction=TransactionDirection.OUTBOUND,
    )
    features = extract_features(
        make_rule_context(
            amount="100.00",
            channel="wire",
            occurred_at=now,
            history=(inbound_prior, outbound_prior),
        )
    )
    assert features["inbound_velocity_24h"] == 1.0
    assert features["inbound_amount_24h_log"] == pytest.approx(math.log1p(400.0))
    assert features["seconds_since_prev_txn_log"] == pytest.approx(math.log1p(2 * 3600.0))
    assert features["distinct_channels_24h"] == 2.0  # wire (current+prior) + ach
    # Round-amount share: current (100.00) + inbound prior (400.00) round, 123.45 not -> 2/3.
    assert features["round_amount_share_24h"] == pytest.approx(2.0 / 3.0)


def test_v2_burstiness_sentinel_without_window_priors(make_rule_context: CtxFactory) -> None:
    features = extract_features(make_rule_context(amount="10.00"))
    assert features["seconds_since_prev_txn_log"] == pytest.approx(math.log1p(86_400.0))
    assert features["inbound_velocity_24h"] == 0.0
    assert features["round_amount_share_24h"] == 0.0  # 10.00 is not a multiple of 100


def test_v2_counterparty_fan_in_features(make_rule_context: CtxFactory) -> None:
    now = datetime(2024, 6, 5, 12, 0, tzinfo=UTC)
    into_dest = RuleTransaction(
        amount=Decimal("900.00"),
        currency="USD",
        country="US",
        channel="wire",
        occurred_at=datetime(2024, 6, 5, 8, 0, tzinfo=UTC),
        direction=TransactionDirection.INBOUND,  # money INTO the destination account
    )
    out_of_dest = RuleTransaction(
        amount=Decimal("850.00"),
        currency="USD",
        country="US",
        channel="wire",
        occurred_at=datetime(2024, 6, 5, 9, 0, tzinfo=UTC),
        direction=TransactionDirection.OUTBOUND,  # the destination passing funds onward
    )
    base = make_rule_context(amount="100.00", occurred_at=now)
    ctx = RuleContext(
        transaction=base.transaction,
        history=base.history,
        counterparty_history=(into_dest, out_of_dest),
    )
    features = extract_features(ctx)
    assert features["dest_fan_in_24h"] == 1.0  # only the inbound leg counts as fan-in
    assert features["dest_inbound_amount_24h_log"] == pytest.approx(math.log1p(100.0 + 900.0))
    # The destination forwarding funds onward is the pass-through/layering-hop signal.
    assert features["dest_outbound_velocity_24h"] == 1.0
    assert features["dest_outbound_amount_24h_log"] == pytest.approx(math.log1p(850.0))
    # Without counterparty history the dest features collapse to the current transaction alone.
    bare = extract_features(base)
    assert bare["dest_fan_in_24h"] == 0.0
    assert bare["dest_inbound_amount_24h_log"] == pytest.approx(math.log1p(100.0))
    assert bare["dest_outbound_velocity_24h"] == 0.0
    assert bare["dest_outbound_amount_24h_log"] == 0.0


def test_feature_vector_orders_by_names_and_rejects_missing() -> None:
    mapping = {name: float(index) for index, name in enumerate(FEATURE_NAMES)}
    vector = feature_vector(mapping)
    assert vector.shape == (1, len(FEATURE_NAMES))
    assert vector[0].tolist() == [float(i) for i in range(len(FEATURE_NAMES))]
    with pytest.raises(ValueError, match="missing required features"):
        feature_vector({"amount_log": 1.0})


def test_extract_feature_vector_matches_extract_features(make_rule_context: CtxFactory) -> None:
    ctx = make_rule_context(amount="42.00")
    vector = extract_feature_vector(ctx)
    assert isinstance(vector, np.ndarray)
    assert vector.shape == (1, len(FEATURE_NAMES))
