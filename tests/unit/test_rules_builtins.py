"""Built-in AML rule tests (plan §16 Phase 4): each of the six deterministic rules fires on
a matching fixture and stays silent otherwise, and the rules degrade to their constant
defaults when params are missing or malformed (robustness underpinning fault isolation)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fraudlens_core import (
    BUILTIN_RULES,
    DEFAULT_RULE_DEFINITIONS,
    AmlRuleType,
    RuleContext,
    RuleDefinition,
    RuleTransaction,
    TransactionDirection,
)

_NOW = datetime(2026, 1, 10, tzinfo=UTC)
_DEFAULTS: dict[AmlRuleType, RuleDefinition] = {d.rule_type: d for d in DEFAULT_RULE_DEFINITIONS}


def _txn(
    amount: str,
    *,
    country: str = "US",
    direction: TransactionDirection = TransactionDirection.OUTBOUND,
    occurred_at: datetime = _NOW,
) -> RuleTransaction:
    """Build a PHI-free RuleTransaction for rule fixtures."""
    return RuleTransaction(
        amount=Decimal(amount),
        currency="USD",
        country=country,
        channel="wire",
        occurred_at=occurred_at,
        direction=direction,
    )


def _eval(rule_type: AmlRuleType, context: RuleContext, definition: RuleDefinition | None = None):
    """Evaluate a built-in rule (default-configured unless a definition is given)."""
    return BUILTIN_RULES[rule_type](definition or _DEFAULTS[rule_type], context)


def test_round_amount_fires_on_whole_multiple_and_silent_otherwise() -> None:
    rule_type = AmlRuleType.ROUND_AMOUNT
    hit = _eval(rule_type, RuleContext(transaction=_txn("5000")))
    assert hit is not None
    assert hit.rule_type is rule_type
    assert hit.details["multipleOf"] == Decimal("1000")
    assert _eval(rule_type, RuleContext(transaction=_txn("5500.50"))) is None


def test_threshold_evasion_fires_just_below_threshold_only() -> None:
    rule_type = AmlRuleType.THRESHOLD_EVASION
    assert _eval(rule_type, RuleContext(transaction=_txn("9500"))) is not None
    assert _eval(rule_type, RuleContext(transaction=_txn("10000"))) is None  # at/above threshold
    assert _eval(rule_type, RuleContext(transaction=_txn("5000"))) is None  # well below the band


def test_high_risk_geography_fires_on_listed_country() -> None:
    rule_type = AmlRuleType.HIGH_RISK_GEOGRAPHY
    hit = _eval(rule_type, RuleContext(transaction=_txn("100", country="ir")))  # case-insensitive
    assert hit is not None
    assert hit.details["country"] == "IR"
    assert _eval(rule_type, RuleContext(transaction=_txn("100", country="US"))) is None


def test_structuring_fires_when_enough_subthreshold_cluster() -> None:
    rule_type = AmlRuleType.STRUCTURING
    history = tuple(_txn("9200", occurred_at=_NOW - timedelta(hours=h)) for h in (2, 20))
    fired = _eval(rule_type, RuleContext(transaction=_txn("9500"), history=history))
    assert fired is not None  # current + 2 history = 3 in band >= minCount
    assert fired.details["count"] == 3
    # One sub-threshold transaction alone does not constitute structuring.
    assert _eval(rule_type, RuleContext(transaction=_txn("9500"))) is None


def test_velocity_fires_above_count_limit_within_window() -> None:
    rule_type = AmlRuleType.VELOCITY
    history = tuple(_txn("10", occurred_at=_NOW - timedelta(hours=h)) for h in range(1, 6))
    fired = _eval(rule_type, RuleContext(transaction=_txn("10"), history=history))
    assert fired is not None  # 1 current + 5 within 24h = 6 > maxCount(5)
    assert fired.details["count"] == 6
    # Transactions outside the window do not count toward velocity.
    stale = tuple(_txn("10", occurred_at=_NOW - timedelta(hours=h)) for h in range(48, 53))
    assert _eval(rule_type, RuleContext(transaction=_txn("10"), history=stale)) is None


def test_rapid_movement_fires_on_in_then_out_only() -> None:
    rule_type = AmlRuleType.RAPID_MOVEMENT
    inbound = (
        _txn("1000", direction=TransactionDirection.INBOUND, occurred_at=_NOW - timedelta(hours=2)),
    )
    out_ctx = RuleContext(
        transaction=_txn("900", direction=TransactionDirection.OUTBOUND), history=inbound
    )
    assert _eval(rule_type, out_ctx) is not None  # outbound follows a comparable inbound
    # An inbound current transaction never completes the in-then-out pattern.
    in_ctx = RuleContext(
        transaction=_txn("900", direction=TransactionDirection.INBOUND), history=inbound
    )
    assert _eval(rule_type, in_ctx) is None
    # An outbound with no preceding inbound does not fire.
    assert _eval(rule_type, RuleContext(transaction=_txn("900"))) is None


def test_rules_fall_back_to_defaults_on_missing_or_garbage_params() -> None:
    # Empty params: the constant defaults still apply (round multiple of 1000 fires on 3000).
    empty = _DEFAULTS[AmlRuleType.ROUND_AMOUNT].model_copy(update={"params": {}})
    assert _eval(AmlRuleType.ROUND_AMOUNT, RuleContext(transaction=_txn("3000")), empty) is not None
    # Garbage numeric/int params fall back rather than raising.
    garbage = _DEFAULTS[AmlRuleType.VELOCITY].model_copy(
        update={"params": {"windowHours": "abc", "maxCount": None}}
    )
    history = tuple(_txn("10", occurred_at=_NOW - timedelta(hours=h)) for h in range(1, 6))
    velocity_ctx = RuleContext(transaction=_txn("10"), history=history)
    assert _eval(AmlRuleType.VELOCITY, velocity_ctx, garbage) is not None
    # A non-numeric Decimal param falls back to the default multiple (fires on 4000).
    bad_decimal = _DEFAULTS[AmlRuleType.ROUND_AMOUNT].model_copy(
        update={"params": {"multipleOf": "xyz"}}
    )
    round_ctx = RuleContext(transaction=_txn("4000"))
    assert _eval(AmlRuleType.ROUND_AMOUNT, round_ctx, bad_decimal) is not None
    # A non-list `countries` param falls back to the default high-risk set.
    geo = _DEFAULTS[AmlRuleType.HIGH_RISK_GEOGRAPHY].model_copy(
        update={"params": {"countries": "IR"}}
    )
    geo_ctx = RuleContext(transaction=_txn("1", country="KP"))
    assert _eval(AmlRuleType.HIGH_RISK_GEOGRAPHY, geo_ctx, geo) is not None
