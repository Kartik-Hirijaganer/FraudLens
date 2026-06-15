"""Rules engine tests (plan §16 Phase 4): weighted aggregation, deterministic results,
disabled rules ignored, per-rule fault isolation (one bad rule skipped, run not aborted),
the version fingerprint, the code-defaults merge precedence, and the layering invariant
(the pure-core rules package imports no ML/backend/heavy dependency)."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import fraudlens_core.rules as rules_pkg
from fraudlens_core import (
    BUILTIN_RULES,
    DEFAULT_RULE_DEFINITIONS,
    AmlRuleType,
    RuleContext,
    RuleDefinition,
    RuleEvaluation,
    RuleHit,
    RuleRegistry,
    RuleTransaction,
    compute_rules_version,
    merge_definitions,
)

_NOW = datetime(2026, 1, 10, tzinfo=UTC)


def _ctx(amount: str = "5000", country: str = "US") -> RuleContext:
    """A single-transaction context (no history) for engine-level tests."""
    return RuleContext(
        transaction=RuleTransaction(
            amount=Decimal(amount),
            currency="USD",
            country=country,
            channel="wire",
            occurred_at=_NOW,
        )
    )


def _fire(code: str, weight: str) -> RuleDefinition:
    """A round_amount rule (fires on 5000) with a given code + weight."""
    return RuleDefinition(
        code=code, name=code, rule_type=AmlRuleType.ROUND_AMOUNT, weight=Decimal(weight)
    )


def _silent(code: str, weight: str) -> RuleDefinition:
    """A high_risk_geography rule that stays silent for a US transaction."""
    return RuleDefinition(
        code=code,
        name=code,
        rule_type=AmlRuleType.HIGH_RISK_GEOGRAPHY,
        params={"countries": ["IR"]},
        weight=Decimal(weight),
    )


def test_subscore_is_weighted_fraction_of_fired_over_evaluated() -> None:
    # fired weight 3 (one fires) over evaluated weight 3+1 = 4 -> 0.75.
    definitions = [_fire("a", "3"), _silent("b", "1")]
    result = RuleRegistry().evaluate(definitions, _ctx())
    assert result.subscore == Decimal("0.7500")
    assert [hit.code for hit in result.hits] == ["a"]
    assert 0 <= result.subscore <= 1


def test_disabled_rules_are_skipped() -> None:
    definitions = [_fire("a", "3").model_copy(update={"enabled": False}), _silent("b", "1")]
    result = RuleRegistry().evaluate(definitions, _ctx())
    # Only the (silent) enabled rule is evaluated: nothing fires -> subscore 0, no hits.
    assert result.hits == ()
    assert result.subscore == Decimal("0")


def test_no_evaluable_rules_yields_zero_subscore() -> None:
    result = RuleRegistry().evaluate([], _ctx())
    assert result.subscore == Decimal("0")
    assert result.rules_version == compute_rules_version([])


def test_evaluation_is_deterministic() -> None:
    first = RuleRegistry().evaluate(DEFAULT_RULE_DEFINITIONS, _ctx("9500"))
    second = RuleRegistry().evaluate(DEFAULT_RULE_DEFINITIONS, _ctx("9500"))
    assert first.model_dump() == second.model_dump()


def test_fault_isolation_skips_a_raising_rule_without_aborting() -> None:
    def boom(_definition: RuleDefinition, _context: RuleContext) -> RuleHit | None:
        raise RuntimeError("buggy rule")

    # ROUND_AMOUNT raises; the real high_risk_geography evaluator runs cleanly (silent for US).
    registry = RuleRegistry(
        {
            AmlRuleType.ROUND_AMOUNT: boom,
            AmlRuleType.HIGH_RISK_GEOGRAPHY: BUILTIN_RULES[AmlRuleType.HIGH_RISK_GEOGRAPHY],
        }
    )
    result = registry.evaluate([_fire("boom", "3"), _silent("ok", "1")], _ctx())
    assert "boom" in result.errored_rules  # the raising rule is isolated...
    assert isinstance(result, RuleEvaluation)  # ...and the run still completes
    # The faulted rule is excluded from numerator AND denominator (only "ok" evaluated).
    assert result.subscore == Decimal("0")


def test_unknown_rule_type_without_evaluator_is_isolated() -> None:
    registry = RuleRegistry({})  # empty dispatch table -> every rule errors
    result = registry.evaluate([_fire("a", "1")], _ctx())
    assert result.errored_rules == ("a",)
    assert result.subscore == Decimal("0")


def test_rules_version_changes_on_version_bump_and_enable_toggle() -> None:
    base = compute_rules_version(DEFAULT_RULE_DEFINITIONS)
    bumped = merge_definitions(
        DEFAULT_RULE_DEFINITIONS, [DEFAULT_RULE_DEFINITIONS[0].model_copy(update={"version": 2})]
    )
    toggled = merge_definitions(
        DEFAULT_RULE_DEFINITIONS,
        [DEFAULT_RULE_DEFINITIONS[0].model_copy(update={"enabled": False})],
    )
    assert compute_rules_version(bumped) != base
    assert compute_rules_version(toggled) != base
    assert compute_rules_version(DEFAULT_RULE_DEFINITIONS) == base  # stable for the same set


def test_merge_definitions_precedence_and_sorting() -> None:
    default = _fire("velocity", "1")
    glob = _fire("velocity", "2")
    agency = _fire("velocity", "3")
    other = _fire("aaa", "1")
    merged = merge_definitions([other, default], [glob], [agency])
    by_code = {definition.code: definition for definition in merged}
    assert by_code["velocity"].weight == Decimal("3")  # agency layer wins
    assert [definition.code for definition in merged] == ["aaa", "velocity"]  # sorted by code


def test_rules_package_imports_no_ml_or_backend_dependency() -> None:
    banned = {
        "fraudlens_ml",
        "fraudlens_backend",
        "xgboost",
        "sklearn",
        "shap",
        "torch",
        "numpy",
        "pandas",
        "chromadb",
        "langchain",
    }
    package_dir = Path(rules_pkg.__path__[0])
    seen: set[str] = set()
    for path in sorted(package_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                seen.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                seen.add(node.module.split(".")[0])
    assert banned.isdisjoint(seen), f"core rules must not import {banned & seen}"
