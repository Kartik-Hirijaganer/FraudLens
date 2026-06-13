"""fraudlens-core: shared domain types and multi-tenancy (agency_id) helpers."""

from fraudlens_core.risk import RiskAssessment, RiskPolicy
from fraudlens_core.rules import (
    BUILTIN_RULES,
    DEFAULT_RULE_DEFINITIONS,
    AmlRuleType,
    RuleContext,
    RuleDefinition,
    RuleEvaluation,
    RuleEvaluator,
    RuleHit,
    RuleRegistry,
    RuleTransaction,
    TransactionDirection,
    compute_rules_version,
    merge_definitions,
)
from fraudlens_core.schema import (
    CanonicalTransaction,
    SchemaValidationError,
    build_canonical,
    compute_feature_hash,
)
from fraudlens_core.tenancy import TenantIsolationError, require_agency_id
from fraudlens_core.types import RiskBand, TransactionSummary

__all__ = [
    "BUILTIN_RULES",
    "DEFAULT_RULE_DEFINITIONS",
    "AmlRuleType",
    "CanonicalTransaction",
    "RiskAssessment",
    "RiskBand",
    "RiskPolicy",
    "RuleContext",
    "RuleDefinition",
    "RuleEvaluation",
    "RuleEvaluator",
    "RuleHit",
    "RuleRegistry",
    "RuleTransaction",
    "SchemaValidationError",
    "TenantIsolationError",
    "TransactionDirection",
    "TransactionSummary",
    "build_canonical",
    "compute_feature_hash",
    "compute_rules_version",
    "merge_definitions",
    "require_agency_id",
]
