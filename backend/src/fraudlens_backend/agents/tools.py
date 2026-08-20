"""Summary: Read-only, tenant-scoped evidence tools for the bounded SAR agent workflow.
Each database capability resolves the verified agency and investigation context through
`TenantScopedRepository` subclasses, opens an independent session per invocation, and returns
only typed identifiers, categorical facts, and numeric aggregates. Regulatory lookup delegates
to the existing pipeline retriever adapter contract instead of opening another ChromaDB path.

Key classes:
- TransactionHistoryItem:
- CurrencyAggregate:
- TransactionHistoryResult: typed transaction identifiers and monetary aggregates.
- RuleHitItem:
- RuleHitsResult: typed persisted rule-hit evidence.
- ShapDriverItem:
- ShapDriversResult: typed persisted model-contribution evidence.
- AlertHistoryItem:
- AlertStatusAggregate:
- AlertHistoryResult: typed prior-alert identifiers and status aggregates.
- RegulationMatch:
- RegulationSearchResult: typed corpus identifiers and relevance metadata.
- ToolSpec: immutable registry entry binding a name, description, JSON schema, and handler.
- EvidenceToolset: run-bound registry and executor for the five approved read-only tools.

Key functions:
- (none)

Notes:
- Tool argument schemas never contain `agency_id`; tenant scope comes only from verified context.
- No capability accepts SQL, command, URL, or write arguments.
- Stored account identifiers, arbitrary transaction features, alert notes, and corpus text are not
returned. The agent runtime additionally masks and fences every serialized result.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.db.models import Alert, AnalysisRun, Transaction
from fraudlens_backend.db.repositories import (
    AnalysisRunRepository,
    TenantScopedRepository,
    TransactionRepository,
)
from fraudlens_llm import ToolDefinition
from fraudlens_ml.pipeline import RagResult

_DEFAULT_HISTORY_WINDOW_HOURS = 168
_DEFAULT_HISTORY_LIMIT = 100
_MAX_REGULATION_RESULTS = 10

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


class _ToolModel(BaseModel):
    """Immutable camelCase boundary shared by tool arguments and results."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


class _NoArguments(_ToolModel):
    """Empty argument boundary for capabilities fixed to the verified investigation context."""


class _RegulationSearchArguments(_ToolModel):
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
        le=_MAX_REGULATION_RESULTS,
        description="Maximum number of relevant corpus chunks to return.",
    )


class TransactionHistoryItem(_ToolModel):
    """One prior tenant-scoped transaction represented without account identifiers."""

    evidence_ref: str = Field(..., description="Stable evidence reference for downstream claims.")
    transaction_id: uuid.UUID = Field(..., description="Persisted transaction identifier.")
    amount: Decimal = Field(..., ge=0, description="Transaction amount.")
    currency: str = Field(..., min_length=3, max_length=3, description="ISO-4217 currency code.")
    occurred_at: datetime = Field(..., description="Timestamp of the persisted transaction.")
    channel: str = Field(..., min_length=1, description="Categorical transaction channel.")
    country: str = Field(..., min_length=2, max_length=2, description="ISO-3166 country code.")


class CurrencyAggregate(_ToolModel):
    """Count and amount aggregate for one currency in transaction history."""

    currency: str = Field(..., min_length=3, max_length=3, description="ISO-4217 currency code.")
    transaction_count: int = Field(..., ge=0, description="Number of matching transactions.")
    total_amount: Decimal = Field(..., ge=0, description="Sum of matching transaction amounts.")


class TransactionHistoryResult(_ToolModel):
    """Bounded same-account history for the transaction owned by the bound investigation."""

    transaction_id: uuid.UUID | None = Field(
        default=None, description="Investigated transaction id, or null when inaccessible."
    )
    transactions: tuple[TransactionHistoryItem, ...] = Field(
        default=(), description="Prior transactions, newest first."
    )
    aggregates: tuple[CurrencyAggregate, ...] = Field(
        default=(), description="Currency-level count and amount aggregates."
    )


class RuleHitItem(_ToolModel):
    """One persisted deterministic rule hit stripped of narrative text."""

    evidence_ref: str = Field(..., description="Stable evidence reference for downstream claims.")
    code: str = Field(..., min_length=1, description="Persisted rule code.")
    rule_type: str = Field(..., min_length=1, description="Categorical AML rule type.")
    severity: str = Field(..., min_length=1, description="Categorical rule severity.")
    weight: Decimal = Field(..., ge=0, description="Persisted rule weight.")


class RuleHitsResult(_ToolModel):
    """Persisted deterministic rule hits for the bound investigation."""

    run_id: uuid.UUID | None = Field(
        default=None, description="Matched investigation id, or null when inaccessible."
    )
    hits: tuple[RuleHitItem, ...] = Field(default=(), description="Validated rule-hit records.")


class ShapDriverItem(_ToolModel):
    """One persisted model feature contribution."""

    evidence_ref: str = Field(..., description="Stable evidence reference for downstream claims.")
    feature: str = Field(..., min_length=1, description="Persisted feature identifier.")
    feature_value: float = Field(..., description="Feature value supplied to the scoring model.")
    shap_value: float = Field(..., description="Signed SHAP contribution for the feature.")


class ShapDriversResult(_ToolModel):
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


class AlertHistoryItem(_ToolModel):
    """One prior alert for the investigated transaction, without notes or user identifiers."""

    evidence_ref: str = Field(..., description="Stable evidence reference for downstream claims.")
    alert_id: uuid.UUID = Field(..., description="Persisted alert identifier.")
    run_id: uuid.UUID = Field(..., description="Investigation identifier that raised the alert.")
    status: str = Field(..., min_length=1, description="Current human-review workflow status.")
    severity: str = Field(..., min_length=1, description="Categorical alert severity.")
    created_at: datetime = Field(..., description="Alert creation timestamp.")


class AlertStatusAggregate(_ToolModel):
    """Count aggregate for one alert status."""

    status: str = Field(..., min_length=1, description="Alert workflow status.")
    alert_count: int = Field(..., ge=0, description="Number of matching alerts in this status.")


class AlertHistoryResult(_ToolModel):
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


class RegulationMatch(_ToolModel):
    """One governed corpus match represented by identifiers and controlled metadata."""

    evidence_ref: str = Field(..., description="Stable evidence reference for downstream claims.")
    chunk_id: str = Field(..., min_length=1, description="Persisted corpus chunk identifier.")
    document_id: str = Field(..., min_length=1, description="Persisted corpus document identifier.")
    citation_id: str = Field(..., min_length=1, description="Exact regulatory citation identifier.")
    title: str = Field(..., min_length=1, description="Governed provision title metadata.")
    source: str = Field(..., min_length=1, description="Governed publisher metadata.")
    relevance_score: float = Field(..., description="Retriever relevance score.")


class RegulationSearchResult(_ToolModel):
    """Bounded regulatory matches produced through the existing retriever adapter."""

    mode: str = Field(..., min_length=1, description="Retriever mode: vector, lexical, or empty.")
    rag_version: str = Field(..., min_length=1, description="Corpus/index version queried.")
    matches: tuple[RegulationMatch, ...] = Field(
        default=(), description="Governed corpus identifiers and relevance metadata."
    )


class _PersistedRuleHit(_ToolModel):
    """Validated subset of the persisted rule-hit JSON record."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
        frozen=True,
    )

    code: str = Field(..., min_length=1, description="Persisted rule code.")
    rule_type: str = Field(..., min_length=1, description="Persisted AML rule type.")
    severity: str = Field(..., min_length=1, description="Persisted rule severity.")
    weight: Decimal = Field(..., ge=0, description="Persisted rule weight.")


class _PersistedShapDriver(_ToolModel):
    """Validated subset of a persisted top-feature JSON record."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
        frozen=True,
    )

    feature: str = Field(..., min_length=1, description="Persisted feature identifier.")
    value: float = Field(..., description="Persisted feature value.")
    shap_value: float = Field(..., description="Persisted SHAP contribution.")


class _PersistedRegulationChunk(_ToolModel):
    """Validated identifier and metadata subset of an adapted retriever chunk."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
        frozen=True,
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


class _RetrieverAdapter(Protocol):
    """Structural view implemented by `pipeline_wiring.RetrieverAdapter`."""

    def retrieve(self, query: str, *, top_k: int) -> RagResult:
        """Return the adapted, citation-fenced retrieval result."""


class _AlertEvidenceRepository(TenantScopedRepository[Alert]):
    """Tenant-scoped read model for alert history used only by agent evidence tools."""

    def __init__(self, session: AsyncSession, agency_id: uuid.UUID) -> None:
        """Bind the alert model and verified tenant scope."""
        super().__init__(session, Alert, agency_id)

    async def for_transaction(self, transaction_id: uuid.UUID) -> Sequence[Alert]:
        """Return this tenant's alerts for one transaction, newest first."""
        statement = (
            select(Alert)
            .where(
                Alert.agency_id == self._agency_id,
                Alert.transaction_id == transaction_id,
            )
            .order_by(Alert.created_at.desc(), Alert.id.desc())
        )
        return (await self._session.execute(statement)).scalars().all()


class EvidenceToolset:
    """Run-bound registry for the five approved, read-only agent capabilities."""

    def __init__(  # noqa: PLR0913 - verified context, adapter, and explicit query bounds.
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        agency_id: uuid.UUID,
        run_id: uuid.UUID,
        *,
        retriever: _RetrieverAdapter,
        history_window_hours: int = _DEFAULT_HISTORY_WINDOW_HOURS,
        history_limit: int = _DEFAULT_HISTORY_LIMIT,
    ) -> None:
        """Bind verified context, independent-session factory, and existing retriever adapter."""
        if history_window_hours <= 0 or history_limit <= 0:
            raise ValueError("History bounds must be positive")
        self._sessionmaker = sessionmaker
        self._agency_id = agency_id
        self._run_id = run_id
        self._retriever = retriever
        self._history_window_hours = history_window_hours
        self._history_limit = history_limit
        no_arguments = _NoArguments.model_json_schema(by_alias=True)
        regulation_arguments = _RegulationSearchArguments.model_json_schema(by_alias=True)
        self._registry: dict[str, ToolSpec] = {
            "transaction_history": ToolSpec(
                name="transaction_history",
                description="Summarize prior transactions for the bound investigation.",
                parameters=no_arguments,
                handler=self._transaction_history,
            ),
            "rule_hits": ToolSpec(
                name="rule_hits",
                description="Return persisted rule identifiers and numeric weights.",
                parameters=no_arguments,
                handler=self._rule_hits,
            ),
            "shap_drivers": ToolSpec(
                name="shap_drivers",
                description="Return persisted feature identifiers and numeric contributions.",
                parameters=no_arguments,
                handler=self._shap_drivers,
            ),
            "alert_history": ToolSpec(
                name="alert_history",
                description="Summarize prior alert identifiers and workflow statuses.",
                parameters=no_arguments,
                handler=self._alert_history,
            ),
            "regulation_search": ToolSpec(
                name="regulation_search",
                description="Find governed regulatory citations for bounded AML concepts.",
                parameters=regulation_arguments,
                handler=self._regulation_search,
            ),
        }
        if self._registry.keys() != AGENT_TOOL_NAMES:
            raise RuntimeError("Agent tool registry does not match its declared capabilities")

    @property
    def registry(self) -> Mapping[str, ToolSpec]:
        """Return an immutable view of the approved capability registry."""
        return self._registry.copy()

    @property
    def definitions(self) -> Mapping[str, ToolDefinition]:
        """Return provider-neutral definitions keyed identically to the registry."""
        return {name: spec.definition() for name, spec in self._registry.items()}

    async def execute(self, name: str, arguments: dict[str, JsonValue]) -> BaseModel:
        """Validate and invoke one named approved capability."""
        spec = self._registry.get(name)
        if spec is None:
            raise ValueError("Unknown evidence capability")
        return await spec.handler(arguments)

    async def _load_context(
        self, session: AsyncSession
    ) -> tuple[AnalysisRun | None, Transaction | None]:
        """Resolve the bound investigation and its transaction through tenant-scoped repos."""
        run = await AnalysisRunRepository(session, self._agency_id).get(self._run_id)
        if run is None:
            return None, None
        transaction = await TransactionRepository(session, self._agency_id).get(run.transaction_id)
        if transaction is None:
            return None, None
        return run, transaction

    async def _transaction_history(
        self, arguments: dict[str, JsonValue]
    ) -> TransactionHistoryResult:
        """Load bounded same-account history in a fresh tenant-scoped session."""
        _NoArguments.model_validate(arguments)
        async with self._sessionmaker() as session:
            _run, transaction = await self._load_context(session)
            if transaction is None:
                return TransactionHistoryResult()
            repository = TransactionRepository(session, self._agency_id)
            rows_by_id: dict[uuid.UUID, Transaction] = {}
            for account in {transaction.origin_account, transaction.dest_account}:
                rows = await repository.same_account_history(
                    account=account,
                    before=transaction.occurred_at,
                    window_hours=self._history_window_hours,
                    limit=self._history_limit,
                )
                rows_by_id.update({row.id: row for row in rows})
            rows = sorted(
                rows_by_id.values(),
                key=lambda row: (row.occurred_at, row.id),
                reverse=True,
            )[: self._history_limit]
            items = tuple(
                TransactionHistoryItem(
                    evidence_ref=f"transaction:{row.id}",
                    transaction_id=row.id,
                    amount=row.amount,
                    currency=row.currency,
                    occurred_at=row.occurred_at,
                    channel=row.channel,
                    country=row.country,
                )
                for row in rows
            )
            totals: dict[str, Decimal] = {}
            counts: Counter[str] = Counter()
            for row in rows:
                totals[row.currency] = totals.get(row.currency, Decimal("0")) + row.amount
                counts[row.currency] += 1
            aggregates = tuple(
                CurrencyAggregate(
                    currency=currency,
                    transaction_count=counts[currency],
                    total_amount=totals[currency],
                )
                for currency in sorted(totals)
            )
            return TransactionHistoryResult(
                transaction_id=transaction.id,
                transactions=items,
                aggregates=aggregates,
            )

    async def _rule_hits(self, arguments: dict[str, JsonValue]) -> RuleHitsResult:
        """Load persisted rule hits in a fresh tenant-scoped session."""
        _NoArguments.model_validate(arguments)
        async with self._sessionmaker() as session:
            result = await AnalysisRunRepository(session, self._agency_id).get_result(self._run_id)
            if result is None:
                return RuleHitsResult()
            hits: list[RuleHitItem] = []
            for index, raw_hit in enumerate(result.rule_hits):
                parsed = _PersistedRuleHit.model_validate(raw_hit)
                hits.append(
                    RuleHitItem(
                        evidence_ref=f"rule-hit:{result.run_id}:{index}",
                        code=parsed.code,
                        rule_type=parsed.rule_type,
                        severity=parsed.severity,
                        weight=parsed.weight,
                    )
                )
            return RuleHitsResult(run_id=result.run_id, hits=tuple(hits))

    async def _shap_drivers(self, arguments: dict[str, JsonValue]) -> ShapDriversResult:
        """Load persisted top feature contributions in a fresh tenant-scoped session."""
        _NoArguments.model_validate(arguments)
        async with self._sessionmaker() as session:
            result = await AnalysisRunRepository(session, self._agency_id).get_result(self._run_id)
            if result is None:
                return ShapDriversResult()
            drivers: list[ShapDriverItem] = []
            for index, raw_driver in enumerate(result.top_features):
                parsed = _PersistedShapDriver.model_validate(raw_driver)
                drivers.append(
                    ShapDriverItem(
                        evidence_ref=f"shap-driver:{result.run_id}:{index}",
                        feature=parsed.feature,
                        feature_value=parsed.value,
                        shap_value=parsed.shap_value,
                    )
                )
            return ShapDriversResult(
                run_id=result.run_id,
                fraud_probability=result.fraud_probability,
                drivers=tuple(drivers),
            )

    async def _alert_history(self, arguments: dict[str, JsonValue]) -> AlertHistoryResult:
        """Load prior alerts for the bound transaction in a fresh tenant-scoped session."""
        _NoArguments.model_validate(arguments)
        async with self._sessionmaker() as session:
            _run, transaction = await self._load_context(session)
            if transaction is None:
                return AlertHistoryResult()
            alerts = await _AlertEvidenceRepository(session, self._agency_id).for_transaction(
                transaction.id
            )
            items = tuple(
                AlertHistoryItem(
                    evidence_ref=f"alert:{alert.id}",
                    alert_id=alert.id,
                    run_id=alert.run_id,
                    status=alert.status.value,
                    severity=alert.severity.value,
                    created_at=alert.created_at,
                )
                for alert in alerts
            )
            counts = Counter(alert.status.value for alert in alerts)
            aggregates = tuple(
                AlertStatusAggregate(status=status, alert_count=counts[status])
                for status in sorted(counts)
            )
            return AlertHistoryResult(
                transaction_id=transaction.id,
                alerts=items,
                status_aggregates=aggregates,
            )

    async def _regulation_search(self, arguments: dict[str, JsonValue]) -> RegulationSearchResult:
        """Delegate a bounded query to the existing adapted retriever path."""
        parsed_arguments = _RegulationSearchArguments.model_validate(arguments)
        result = self._retriever.retrieve(
            parsed_arguments.query,
            top_k=parsed_arguments.top_k,
        )
        matches: list[RegulationMatch] = []
        for raw_chunk in result.chunks:
            chunk = _PersistedRegulationChunk.model_validate(raw_chunk)
            matches.append(
                RegulationMatch(
                    evidence_ref=f"regulation:{chunk.chunk_id}",
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.doc_id,
                    citation_id=chunk.citation,
                    title=chunk.title,
                    source=chunk.source,
                    relevance_score=chunk.score,
                )
            )
        return RegulationSearchResult(
            mode=result.mode,
            rag_version=result.rag_version,
            matches=tuple(matches),
        )
