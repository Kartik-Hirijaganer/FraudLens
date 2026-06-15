"""fraudlens-core deterministic AML rules engine (plan §16 Phase 4): the canonical rule
taxonomy, the PHI-free evaluation value model, the six built-in rules, and the engine that
aggregates a weighted subscore with per-rule fault isolation. Pure Python — no ML, no DB, no
framework. Re-exports are intentional (the public engine surface)."""

from fraudlens_core.rules.base import (
    AmlRuleType,
    RuleContext,
    RuleDefinition,
    RuleEvaluation,
    RuleEvaluator,
    RuleHit,
    RuleTransaction,
    TransactionDirection,
)
from fraudlens_core.rules.builtins import BUILTIN_RULES, DEFAULT_RULE_DEFINITIONS
from fraudlens_core.rules.registry import RuleRegistry, compute_rules_version, merge_definitions

__all__ = [
    "BUILTIN_RULES",
    "DEFAULT_RULE_DEFINITIONS",
    "AmlRuleType",
    "RuleContext",
    "RuleDefinition",
    "RuleEvaluation",
    "RuleEvaluator",
    "RuleHit",
    "RuleRegistry",
    "RuleTransaction",
    "TransactionDirection",
    "compute_rules_version",
    "merge_definitions",
]
