"""Summary: The six built-in deterministic AML rule evaluators and their code-default
definitions (plan §16 Phase 4). Each evaluator is a pure function `(RuleDefinition,
RuleContext) -> RuleHit | None` that reads its tunables from the definition's `params` (so
nothing business-specific is hardcoded, rule 4 — fallbacks come from the named constants
that ALSO seed `DEFAULT_RULE_DEFINITIONS`, one source of truth) and returns a PHI-free hit
when the pattern fires, else None. `BUILTIN_RULES` maps each `AmlRuleType` to its evaluator
(the engine's dispatch table); `DEFAULT_RULE_DEFINITIONS` is the baseline rule set the DB
seed loads and the engine merges DB overrides onto, so the rules work even with an empty/
unavailable `aml_rules` table (graceful degradation, plan §11).

Key classes:
- (none)

Key functions:
- (none)

Notes:
- Stateless rules (round_amount, threshold_evasion, high_risk_geography) read only the
  current transaction; stateful rules (structuring, velocity, rapid_movement) also read the
  PHI-free, same-account `history` the caller pre-grouped (no identifiers reach the engine).
- Evaluators are individually robust: a missing/garbage param falls back to its constant
  default rather than raising, and any genuine fault is isolated by the engine (registry.py).
- `details`/`reason` carry only counts, thresholds, and the non-PHI country code — never an
  amount-derived identifier — so a hit is safe to persist or log (plan §8.4).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from fraudlens_core.rules.base import (
    AmlRuleType,
    RuleContext,
    RuleDefinition,
    RuleEvaluator,
    RuleHit,
    RuleTransaction,
    TransactionDirection,
)

# Recommended defaults — the single source of truth shared by the evaluator fallbacks and
# DEFAULT_RULE_DEFINITIONS, so neither a magic literal nor a duplicated value appears.
_REPORTING_THRESHOLD = Decimal("10000")
_SUBTHRESHOLD_MARGIN = Decimal("0.1")
_STRUCTURING_WINDOW_HOURS = 168
_STRUCTURING_MIN_COUNT = 3
_VELOCITY_WINDOW_HOURS = 24
_VELOCITY_MAX_COUNT = 5
_ROUND_MULTIPLE = Decimal("1000")
_RAPID_WINDOW_HOURS = 48
_RAPID_MIN_RATIO = Decimal("0.8")
_HIGH_RISK_COUNTRIES: tuple[str, ...] = ("IR", "KP", "SY", "CU", "RU")


def _decimal_param(params: dict[str, Any], key: str, default: Decimal) -> Decimal:
    """Return a positive, finite Decimal param, falling back to `default` on any problem."""
    raw = params.get(key)
    if raw is None:
        return default
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return default
    return value if value.is_finite() else default


def _int_param(params: dict[str, Any], key: str, default: int) -> int:
    """Return an int param, falling back to `default` when absent or un-coercible."""
    raw = params.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _country_set(params: dict[str, Any]) -> frozenset[str]:
    """Return the upper-cased high-risk country set from params (default constant list)."""
    raw = params.get("countries")
    values = raw if isinstance(raw, list | tuple) else _HIGH_RISK_COUNTRIES
    return frozenset(str(item).strip().upper() for item in values if str(item).strip())


def _num(value: Decimal) -> str:
    """Render a Decimal without exponent/trailing-zero noise (for human-readable reasons)."""
    return format(value.normalize(), "f")


def _in_window(
    history: tuple[RuleTransaction, ...], reference: RuleTransaction, window_hours: int
) -> list[RuleTransaction]:
    """Return history transactions whose occurred_at is within the window up to `reference`."""
    start = reference.occurred_at - timedelta(hours=window_hours)
    return [item for item in history if start <= item.occurred_at <= reference.occurred_at]


def _hit(definition: RuleDefinition, reason: str, details: dict[str, Any]) -> RuleHit:
    """Build a RuleHit, carrying the definition's identity/severity/weight (DRY helper)."""
    return RuleHit(
        code=definition.code,
        rule_type=definition.rule_type,
        severity=definition.severity,
        weight=definition.weight,
        reason=reason,
        details=details,
    )


def _eval_round_amount(definition: RuleDefinition, context: RuleContext) -> RuleHit | None:
    """Fire when the amount is a positive whole multiple of the configured `multipleOf`."""
    multiple = _decimal_param(definition.params, "multipleOf", _ROUND_MULTIPLE)
    amount = context.transaction.amount
    if multiple > 0 and amount >= multiple and amount % multiple == 0:
        return _hit(
            definition,
            f"Amount is a round multiple of {_num(multiple)}.",
            {"multipleOf": multiple},
        )
    return None


def _eval_threshold_evasion(definition: RuleDefinition, context: RuleContext) -> RuleHit | None:
    """Fire when a single amount sits in the band just below the reporting threshold."""
    threshold = _decimal_param(definition.params, "threshold", _REPORTING_THRESHOLD)
    margin = _decimal_param(definition.params, "marginPct", _SUBTHRESHOLD_MARGIN)
    lower = threshold * (Decimal("1") - margin)
    if lower <= context.transaction.amount < threshold:
        return _hit(
            definition,
            f"Amount sits just below the {_num(threshold)} reporting threshold.",
            {"threshold": threshold, "marginPct": margin},
        )
    return None


def _eval_high_risk_geography(definition: RuleDefinition, context: RuleContext) -> RuleHit | None:
    """Fire when the transaction country is on the configured high-risk list."""
    country = context.transaction.country.strip().upper()
    if country in _country_set(definition.params):
        return _hit(
            definition,
            f"Destination country {country} is on the high-risk list.",
            {"country": country},
        )
    return None


def _eval_structuring(definition: RuleDefinition, context: RuleContext) -> RuleHit | None:
    """Fire when enough sub-threshold transactions cluster within the window (structuring)."""
    threshold = _decimal_param(definition.params, "threshold", _REPORTING_THRESHOLD)
    margin = _decimal_param(definition.params, "marginPct", _SUBTHRESHOLD_MARGIN)
    window_hours = _int_param(definition.params, "windowHours", _STRUCTURING_WINDOW_HOURS)
    min_count = _int_param(definition.params, "minCount", _STRUCTURING_MIN_COUNT)
    lower = threshold * (Decimal("1") - margin)
    current = context.transaction
    candidates = [current, *_in_window(context.history, current, window_hours)]
    count = sum(1 for item in candidates if lower <= item.amount < threshold)
    if count >= min_count:
        return _hit(
            definition,
            f"{count} sub-threshold transactions within {window_hours}h suggest structuring.",
            {"count": count, "minCount": min_count, "windowHours": window_hours},
        )
    return None


def _eval_velocity(definition: RuleDefinition, context: RuleContext) -> RuleHit | None:
    """Fire when the transaction count within the window exceeds the configured maximum."""
    window_hours = _int_param(definition.params, "windowHours", _VELOCITY_WINDOW_HOURS)
    max_count = _int_param(definition.params, "maxCount", _VELOCITY_MAX_COUNT)
    count = 1 + len(_in_window(context.history, context.transaction, window_hours))
    if count > max_count:
        return _hit(
            definition,
            f"{count} transactions in {window_hours}h exceed the velocity limit of {max_count}.",
            {"count": count, "maxCount": max_count, "windowHours": window_hours},
        )
    return None


def _eval_rapid_movement(definition: RuleDefinition, context: RuleContext) -> RuleHit | None:
    """Fire when an outbound follows a comparable inbound within the window (in-then-out)."""
    current = context.transaction
    if current.direction is not TransactionDirection.OUTBOUND:
        return None
    window_hours = _int_param(definition.params, "windowHours", _RAPID_WINDOW_HOURS)
    min_ratio = _decimal_param(definition.params, "minRatio", _RAPID_MIN_RATIO)
    floor = current.amount * min_ratio
    has_inbound = any(
        item.direction is TransactionDirection.INBOUND and item.amount >= floor
        for item in _in_window(context.history, current, window_hours)
    )
    if has_inbound:
        return _hit(
            definition,
            f"Inbound funds moved out within {window_hours}h (rapid movement).",
            {"windowHours": window_hours, "minRatio": min_ratio},
        )
    return None


# The engine's dispatch table: each rule type maps to exactly one built-in evaluator.
BUILTIN_RULES: dict[AmlRuleType, RuleEvaluator] = {
    AmlRuleType.ROUND_AMOUNT: _eval_round_amount,
    AmlRuleType.THRESHOLD_EVASION: _eval_threshold_evasion,
    AmlRuleType.HIGH_RISK_GEOGRAPHY: _eval_high_risk_geography,
    AmlRuleType.STRUCTURING: _eval_structuring,
    AmlRuleType.VELOCITY: _eval_velocity,
    AmlRuleType.RAPID_MOVEMENT: _eval_rapid_movement,
}


# The baseline rule set: seeded into `aml_rules` (global) and merged under any DB overrides.
DEFAULT_RULE_DEFINITIONS: tuple[RuleDefinition, ...] = (
    RuleDefinition(
        code="structuring",
        name="Structuring (sub-threshold clustering)",
        rule_type=AmlRuleType.STRUCTURING,
        params={
            "threshold": int(_REPORTING_THRESHOLD),
            "marginPct": float(_SUBTHRESHOLD_MARGIN),
            "windowHours": _STRUCTURING_WINDOW_HOURS,
            "minCount": _STRUCTURING_MIN_COUNT,
        },
        severity="high",
        weight=Decimal("2.0"),
    ),
    RuleDefinition(
        code="velocity",
        name="Velocity (transaction frequency)",
        rule_type=AmlRuleType.VELOCITY,
        params={"windowHours": _VELOCITY_WINDOW_HOURS, "maxCount": _VELOCITY_MAX_COUNT},
        severity="medium",
        weight=Decimal("1.0"),
    ),
    RuleDefinition(
        code="high_risk_geography",
        name="High-risk geography",
        rule_type=AmlRuleType.HIGH_RISK_GEOGRAPHY,
        params={"countries": list(_HIGH_RISK_COUNTRIES)},
        severity="high",
        weight=Decimal("1.5"),
    ),
    RuleDefinition(
        code="round_amount",
        name="Round-amount transaction",
        rule_type=AmlRuleType.ROUND_AMOUNT,
        params={"multipleOf": int(_ROUND_MULTIPLE)},
        severity="low",
        weight=Decimal("0.5"),
    ),
    RuleDefinition(
        code="threshold_evasion",
        name="Reporting-threshold evasion",
        rule_type=AmlRuleType.THRESHOLD_EVASION,
        params={"threshold": int(_REPORTING_THRESHOLD), "marginPct": float(_SUBTHRESHOLD_MARGIN)},
        severity="high",
        weight=Decimal("2.0"),
    ),
    RuleDefinition(
        code="rapid_movement",
        name="Rapid movement of funds",
        rule_type=AmlRuleType.RAPID_MOVEMENT,
        params={"windowHours": _RAPID_WINDOW_HOURS, "minRatio": float(_RAPID_MIN_RATIO)},
        severity="medium",
        weight=Decimal("1.5"),
    ),
)
