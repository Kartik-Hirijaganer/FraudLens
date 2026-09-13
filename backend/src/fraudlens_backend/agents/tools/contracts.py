"""Summary: Typed contracts for the bounded, read-only SAR evidence tools.

Key classes:
- ToolModel:
- NoArguments:
- RegulationSearchArguments:
- TransactionHistoryItem, TransactionHistoryResult: typed transaction evidence.
- CurrencyAggregate:
- TransactionHistoryResult:
- RuleHitItem, RuleHitsResult: typed persisted rule-hit evidence.
- RuleHitsResult:
- ShapDriverItem, ShapDriversResult: typed model-contribution evidence.
- ShapDriversResult:
- AlertHistoryItem, AlertHistoryResult: typed prior-alert evidence.
- AlertStatusAggregate:
- AlertHistoryResult:
- RegulationMatch, RegulationSearchResult: typed corpus evidence.
- RegulationSearchResult:
- PersistedRuleHit:
- PersistedShapDriver:
- PersistedRegulationChunk:
- ToolSpec: immutable registry entry for an approved capability.

Key functions:
- (none)

Notes:
- Argument schemas never accept agency identifiers; verified runtime context supplies tenancy.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic.alias_generators import to_camel

from fraudlens_llm import ToolDefinition

MAX_REGULATION_RESULTS = 10
AGENT_TOOL_NAMES = frozenset(
    {
        "transaction_history",
        "rule_hits",
        "shap_drivers",
        "alert_history",
        "regulation_search",
    }
)
ToolHandler = Callable[[dict[str, JsonValue]], Awaitable[BaseModel]]


class ToolModel(BaseModel):
    """Immutable camelCase boundary shared by tool arguments and results."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


class NoArguments(ToolModel):
    """Empty arguments for capabilities fixed to the verified investigation context."""


class RegulationSearchArguments(ToolModel):
    """Bounded regulatory-corpus lookup arguments."""

    query: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="Concise AML concepts to match against the governed corpus.",
    )
    top_k: int = Field(
        ...,
        ge=1,
        le=MAX_REGULATION_RESULTS,
        description="Maximum number of relevant corpus chunks to return.",
    )


class TransactionHistoryItem(ToolModel):
    """One prior tenant-scoped transaction represented without account identifiers."""

    evidence_ref: str = Field(..., description="Stable evidence reference for downstream claims.")
    transaction_id: uuid.UUID = Field(..., description="Persisted transaction identifier.")
    amount: Decimal = Field(..., ge=0, description="Transaction amount.")
    currency: str = Field(..., min_length=3, max_length=3, description="ISO-4217 currency code.")
    occurred_at: datetime = Field(..., description="Timestamp of the persisted transaction.")
    channel: str = Field(..., min_length=1, description="Categorical transaction channel.")
    country: str = Field(..., min_length=2, max_length=2, description="ISO-3166 country code.")


class CurrencyAggregate(ToolModel):
    """Count and amount aggregate for one currency in transaction history."""

    currency: str = Field(..., min_length=3, max_length=3, description="ISO-4217 currency code.")
    transaction_count: int = Field(..., ge=0, description="Number of matching transactions.")
    total_amount: Decimal = Field(..., ge=0, description="Sum of matching transaction amounts.")


class TransactionHistoryResult(ToolModel):
    """Bounded same-account history for the transaction owned by the investigation."""

    transaction_id: uuid.UUID | None = Field(
        default=None, description="Investigated transaction id, or null when inaccessible."
    )
    transactions: tuple[TransactionHistoryItem, ...] = Field(
        default=(), description="Prior transactions, newest first."
    )
    aggregates: tuple[CurrencyAggregate, ...] = Field(
        default=(), description="Currency-level count and amount aggregates."
    )


class RuleHitItem(ToolModel):
    """One persisted deterministic rule hit stripped of narrative text."""

    evidence_ref: str = Field(..., description="Stable evidence reference for downstream claims.")
    code: str = Field(..., min_length=1, description="Persisted rule code.")
    rule_type: str = Field(..., min_length=1, description="Categorical AML rule type.")
    severity: str = Field(..., min_length=1, description="Categorical rule severity.")
    weight: Decimal = Field(..., ge=0, description="Persisted rule weight.")


class RuleHitsResult(ToolModel):
    """Persisted deterministic rule hits for the bound investigation."""

    run_id: uuid.UUID | None = Field(
        default=None, description="Matched investigation id, or null when inaccessible."
    )
    hits: tuple[RuleHitItem, ...] = Field(default=(), description="Validated rule-hit records.")


class ShapDriverItem(ToolModel):
    """One persisted model feature contribution."""

    evidence_ref: str = Field(..., description="Stable evidence reference for downstream claims.")
    feature: str = Field(..., min_length=1, description="Persisted feature identifier.")
    feature_value: float = Field(..., description="Feature value supplied to the scoring model.")
    shap_value: float = Field(..., description="Signed SHAP contribution for the feature.")


class ShapDriversResult(ToolModel):
    """Persisted top model contributions for the bound investigation."""

    run_id: uuid.UUID | None = Field(
        default=None, description="Matched investigation id, or null when inaccessible."
    )
    fraud_probability: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Persisted model fraud probability, or null when inaccessible.",
    )
    drivers: tuple[ShapDriverItem, ...] = Field(
        default=(), description="Validated feature contributions in persisted order."
    )


class AlertHistoryItem(ToolModel):
    """One prior alert without notes or user identifiers."""

    evidence_ref: str = Field(..., description="Stable evidence reference for downstream claims.")
    alert_id: uuid.UUID = Field(..., description="Persisted alert identifier.")
    run_id: uuid.UUID = Field(..., description="Investigation identifier that raised the alert.")
    status: str = Field(..., min_length=1, description="Current review workflow status.")
    severity: str = Field(..., min_length=1, description="Categorical alert severity.")
    created_at: datetime = Field(..., description="Alert creation timestamp.")


class AlertStatusAggregate(ToolModel):
    """Count aggregate for one alert status."""

    status: str = Field(..., min_length=1, description="Alert workflow status.")
    alert_count: int = Field(..., ge=0, description="Number of matching alerts in this status.")


class AlertHistoryResult(ToolModel):
    """Tenant-scoped alert history for the bound investigation's transaction."""

    transaction_id: uuid.UUID | None = Field(
        default=None, description="Investigated transaction id, or null when inaccessible."
    )
    alerts: tuple[AlertHistoryItem, ...] = Field(
        default=(), description="Matching alert identifiers, newest first."
    )
    status_aggregates: tuple[AlertStatusAggregate, ...] = Field(
        default=(), description="Counts grouped by alert workflow status."
    )


class RegulationMatch(ToolModel):
    """One governed corpus match represented by identifiers and controlled metadata."""

    evidence_ref: str = Field(..., description="Stable evidence reference for downstream claims.")
    chunk_id: str = Field(..., min_length=1, description="Persisted corpus chunk identifier.")
    document_id: str = Field(..., min_length=1, description="Persisted corpus document identifier.")
    citation_id: str = Field(..., min_length=1, description="Exact regulatory citation identifier.")
    title: str = Field(..., min_length=1, description="Governed provision title metadata.")
    source: str = Field(..., min_length=1, description="Governed publisher metadata.")
    relevance_score: float = Field(..., description="Retriever relevance score.")


class RegulationSearchResult(ToolModel):
    """Bounded regulatory matches produced through the existing retriever adapter."""

    mode: str = Field(..., min_length=1, description="Retriever mode: vector, lexical, or empty.")
    rag_version: str = Field(..., min_length=1, description="Corpus/index version queried.")
    matches: tuple[RegulationMatch, ...] = Field(
        default=(), description="Governed corpus identifiers and relevance metadata."
    )


class PersistedRuleHit(ToolModel):
    """Validated subset of the persisted rule-hit JSON record."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="ignore", frozen=True
    )
    code: str = Field(..., min_length=1, description="Persisted rule code.")
    rule_type: str = Field(..., min_length=1, description="Persisted AML rule type.")
    severity: str = Field(..., min_length=1, description="Persisted rule severity.")
    weight: Decimal = Field(..., ge=0, description="Persisted rule weight.")


class PersistedShapDriver(ToolModel):
    """Validated subset of a persisted top-feature JSON record."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="ignore", frozen=True
    )
    feature: str = Field(..., min_length=1, description="Persisted feature identifier.")
    value: float = Field(..., description="Persisted feature value.")
    shap_value: float = Field(..., description="Persisted SHAP contribution.")


class PersistedRegulationChunk(ToolModel):
    """Validated identifier and metadata subset of an adapted retriever chunk."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="ignore", frozen=True
    )
    chunk_id: str = Field(..., min_length=1, description="Persisted corpus chunk identifier.")
    doc_id: str = Field(..., min_length=1, description="Persisted corpus document identifier.")
    citation: str = Field(..., min_length=1, description="Exact regulatory citation.")
    title: str = Field(..., min_length=1, description="Governed provision title metadata.")
    source: str = Field(..., min_length=1, description="Governed publisher metadata.")
    score: float = Field(..., description="Retriever relevance score.")


class ToolSpec(BaseModel):
    """Immutable approved-tool registry entry."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)
    name: str = Field(..., min_length=1, description="Stable approved capability name.")
    description: str = Field(..., min_length=1, description="Provider-facing capability summary.")
    parameters: dict[str, JsonValue] = Field(
        ..., description="JSON Schema for the capability's model-supplied arguments."
    )
    handler: ToolHandler = Field(..., description="Bound read-only capability handler.")

    def definition(self) -> ToolDefinition:
        """Project this registry entry onto the provider-neutral LLM tool contract."""
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
        )
