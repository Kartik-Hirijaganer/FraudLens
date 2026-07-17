"""Summary: The framework-agnostic value model for the deterministic AML rules engine
(plan §16 Phase 4). It defines the canonical rule taxonomy (`AmlRuleType`) — kept here in
`fraudlens-core` so the engine can dispatch on it and the backend ORM column can reuse it,
exactly like `RiskBand` (no duplication, rule 5; layering keeps `core` import-free of the
backend). A `RuleDefinition` is the tunable config for one rule (type + params + severity +
weight + enabled + version); a `RuleContext` is the PHI-free input the rules evaluate — the
transaction under review plus a same-account history pre-grouped by the caller; a `RuleHit`
is one rule's typed, PHI-free finding; a `RuleEvaluation` is the aggregate (hits + weighted
subscore + a deterministic rules-version fingerprint + any fault-isolated rule codes).

Key classes:
- AmlRuleType: the canonical kind of deterministic AML rule (engine dispatch key).
- TransactionDirection: whether a transaction is inbound to or outbound from the account.
- RuleTransaction: the PHI-free analytical view of a transaction (current or prior).
- RuleContext: the input to evaluation — the transaction plus same-account history.
- RuleDefinition: the tunable configuration of a single rule.
- RuleHit: one rule's typed, PHI-free finding when it fires.
- RuleEvaluation: the aggregate result — hits, weighted subscore, version, errored codes.

Key functions:
- (none)

Notes:
- `RuleTransaction` deliberately carries NO account identifiers: history is pre-grouped by
  account by the caller, so the engine stays PHI-free by construction (plan §8.4).
- `RuleContext.counterparty_history` (destination-account activity, directions relative to the
  destination) exists for the feature extractor's fan-in signals; the rules engine ignores it.
- `RuleHit.reason`/`details` are value-free of PHI — only counts, thresholds, and the
  (non-PHI) country code — so a hit can flow into `analysis_results`/logs without leaking.
- Every field carries `Field(..., description=...)` and models are frozen with
  `extra="forbid"` (Pydantic-boundary rule 1).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AmlRuleType(StrEnum):
    """The canonical kind of deterministic AML rule; the engine dispatch key (plan §9.1)."""

    STRUCTURING = "structuring"
    VELOCITY = "velocity"
    HIGH_RISK_GEOGRAPHY = "high_risk_geography"
    ROUND_AMOUNT = "round_amount"
    THRESHOLD_EVASION = "threshold_evasion"
    RAPID_MOVEMENT = "rapid_movement"


class TransactionDirection(StrEnum):
    """Whether a transaction moves funds into or out of the account under review."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"


class RuleTransaction(BaseModel):
    """The PHI-free analytical view of a transaction the rules evaluate (no identifiers)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    amount: Decimal = Field(..., gt=0, description="Transaction amount (positive).")
    currency: str = Field(..., description="Normalized ISO-4217 currency code.")
    country: str = Field(..., description="Normalized ISO-3166 alpha-2 country code.")
    channel: str = Field(..., description="Origination channel, e.g. 'wire' or 'card'.")
    occurred_at: datetime = Field(..., description="When the transaction occurred (tz-aware).")
    direction: TransactionDirection = Field(
        default=TransactionDirection.OUTBOUND,
        description="Whether the transaction is inbound to or outbound from the account.",
    )


class RuleContext(BaseModel):
    """The input to rule evaluation: the transaction under review + same-account history."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    transaction: RuleTransaction = Field(..., description="The transaction being evaluated.")
    history: tuple[RuleTransaction, ...] = Field(
        default=(),
        description="Recent same-account activity (PHI-free), pre-grouped by the caller.",
    )
    counterparty_history: tuple[RuleTransaction, ...] = Field(
        default=(),
        description="Recent destination-account activity (PHI-free), pre-grouped by the caller "
        "with directions relative to the DESTINATION account; rules ignore it, the feature "
        "extractor uses it for counterparty fan-in signals (feature-spec v2).",
    )


class RuleDefinition(BaseModel):
    """The tunable configuration of a single deterministic rule (DB row or code default)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(..., min_length=1, description="Stable per-rule identifier (merge key).")
    name: str = Field(..., description="Human-readable rule name.")
    rule_type: AmlRuleType = Field(..., description="Which built-in evaluator runs this rule.")
    params: dict[str, Any] = Field(
        default_factory=dict, description="Rule-type-specific tunables (camelCase JSONB keys)."
    )
    severity: str = Field(
        default="medium", description="Ordinal severity carried through to the hit (opaque str)."
    )
    weight: Decimal = Field(
        default=Decimal("1.0"), gt=0, description="Aggregation weight of this rule's contribution."
    )
    enabled: bool = Field(default=True, description="Disabled rules are skipped by the engine.")
    version: int = Field(default=1, ge=1, description="Monotonic version; bumped on every edit.")


class RuleHit(BaseModel):
    """One rule's typed, PHI-free finding produced when the rule fires."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(..., description="The firing rule's stable code.")
    rule_type: AmlRuleType = Field(..., description="The firing rule's type.")
    severity: str = Field(..., description="The firing rule's severity (carried from its config).")
    weight: Decimal = Field(..., description="The firing rule's aggregation weight.")
    reason: str = Field(..., description="Fixed, PHI-free explanation of why the rule fired.")
    details: dict[str, Any] = Field(
        default_factory=dict, description="PHI-free numeric context (counts, thresholds, country)."
    )


class RuleEvaluation(BaseModel):
    """The aggregate evaluation result for a transaction across all evaluated rules."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    hits: tuple[RuleHit, ...] = Field(
        default=(), description="The rules that fired (in definition order)."
    )
    subscore: Decimal = Field(
        ..., ge=0, le=1, description="Weighted deterministic rules subscore in [0, 1]."
    )
    rules_version: str = Field(
        ..., description="Deterministic fingerprint of the evaluated rule set (code+version+state)."
    )
    errored_rules: tuple[str, ...] = Field(
        default=(), description="Codes of rules skipped by fault isolation (run not aborted)."
    )


# An evaluator turns one rule definition + a context into a hit (fired) or None (silent).
# It is a module-level type alias (not part of the header inventory).
RuleEvaluator = Callable[[RuleDefinition, RuleContext], "RuleHit | None"]
