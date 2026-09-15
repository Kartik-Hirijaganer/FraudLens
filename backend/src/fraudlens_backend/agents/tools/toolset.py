"""Summary: Tenant-scoped implementation of the approved SAR evidence-tool registry.

Key classes:
- RetrieverAdapter:
- AlertEvidenceRepository:
- EvidenceToolset: run-bound executor for the five approved read-only capabilities.

Key functions:
- (none)

Notes:
- Every database lookup opens a fresh session and applies the verified agency scope.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.agents.tools.contracts import (
    AGENT_TOOL_NAMES,
    AlertHistoryItem,
    AlertHistoryResult,
    AlertStatusAggregate,
    CurrencyAggregate,
    NoArguments,
    PersistedRegulationChunk,
    PersistedRuleHit,
    PersistedShapDriver,
    RegulationMatch,
    RegulationSearchArguments,
    RegulationSearchResult,
    RuleHitItem,
    RuleHitsResult,
    ShapDriverItem,
    ShapDriversResult,
    ToolSpec,
    TransactionHistoryItem,
    TransactionHistoryResult,
)
from fraudlens_backend.db.models import Alert, AnalysisRun, Transaction
from fraudlens_backend.db.repositories import (
    AnalysisRunRepository,
    TenantScopedRepository,
    TransactionRepository,
)
from fraudlens_llm import ToolDefinition
from fraudlens_ml.pipeline import RagResult

DEFAULT_HISTORY_WINDOW_HOURS = 168
DEFAULT_HISTORY_LIMIT = 100


class RetrieverAdapter(Protocol):
    """Structural view implemented by the pipeline retriever adapter."""

    def retrieve(self, query: str, *, top_k: int) -> RagResult:
        """Return the adapted, citation-fenced retrieval result."""


class AlertEvidenceRepository(TenantScopedRepository[Alert]):
    """Tenant-scoped read model for alert history used only by evidence tools."""

    def __init__(self, session: AsyncSession, agency_id: uuid.UUID) -> None:
        """Bind the alert model and verified tenant scope."""
        super().__init__(session, Alert, agency_id)

    async def for_transaction(self, transaction_id: uuid.UUID) -> Sequence[Alert]:
        """Return this tenant's alerts for one transaction, newest first."""
        statement = (
            select(Alert)
            .where(Alert.agency_id == self._agency_id, Alert.transaction_id == transaction_id)
            .order_by(Alert.created_at.desc(), Alert.id.desc())
        )
        return (await self._session.execute(statement)).scalars().all()


class EvidenceToolset:
    """Run-bound registry for the five approved, read-only agent capabilities."""

    def __init__(  # noqa: PLR0913
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        agency_id: uuid.UUID,
        run_id: uuid.UUID,
        *,
        retriever: RetrieverAdapter,
        history_window_hours: int = DEFAULT_HISTORY_WINDOW_HOURS,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
    ) -> None:
        """Bind verified context, independent-session factory, and retriever adapter."""
        if history_window_hours <= 0 or history_limit <= 0:
            raise ValueError("History bounds must be positive")
        self._sessionmaker = sessionmaker
        self._agency_id = agency_id
        self._run_id = run_id
        self._retriever = retriever
        self._history_window_hours = history_window_hours
        self._history_limit = history_limit
        no_arguments = NoArguments.model_json_schema(by_alias=True)
        regulation_arguments = RegulationSearchArguments.model_json_schema(by_alias=True)
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
        run = await AnalysisRunRepository(session, self._agency_id).get(self._run_id)
        if run is None:
            return None, None
        transaction = await TransactionRepository(session, self._agency_id).get(run.transaction_id)
        return (run, transaction) if transaction is not None else (None, None)

    async def _transaction_history(
        self, arguments: dict[str, JsonValue]
    ) -> TransactionHistoryResult:
        NoArguments.model_validate(arguments)
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
                rows_by_id.values(), key=lambda row: (row.occurred_at, row.id), reverse=True
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
                transaction_id=transaction.id, transactions=items, aggregates=aggregates
            )

    async def _rule_hits(self, arguments: dict[str, JsonValue]) -> RuleHitsResult:
        NoArguments.model_validate(arguments)
        async with self._sessionmaker() as session:
            result = await AnalysisRunRepository(session, self._agency_id).get_result(self._run_id)
            if result is None:
                return RuleHitsResult()
            hits = tuple(
                RuleHitItem(
                    evidence_ref=f"rule-hit:{result.run_id}:{index}",
                    code=parsed.code,
                    rule_type=parsed.rule_type,
                    severity=parsed.severity,
                    weight=parsed.weight,
                )
                for index, raw_hit in enumerate(result.rule_hits)
                for parsed in (PersistedRuleHit.model_validate(raw_hit),)
            )
            return RuleHitsResult(run_id=result.run_id, hits=hits)

    async def _shap_drivers(self, arguments: dict[str, JsonValue]) -> ShapDriversResult:
        NoArguments.model_validate(arguments)
        async with self._sessionmaker() as session:
            result = await AnalysisRunRepository(session, self._agency_id).get_result(self._run_id)
            if result is None:
                return ShapDriversResult()
            drivers = tuple(
                ShapDriverItem(
                    evidence_ref=f"shap-driver:{result.run_id}:{index}",
                    feature=parsed.feature,
                    feature_value=parsed.value,
                    shap_value=parsed.shap_value,
                )
                for index, raw_driver in enumerate(result.top_features)
                for parsed in (PersistedShapDriver.model_validate(raw_driver),)
            )
            return ShapDriversResult(
                run_id=result.run_id,
                fraud_probability=result.fraud_probability,
                drivers=drivers,
            )

    async def _alert_history(self, arguments: dict[str, JsonValue]) -> AlertHistoryResult:
        NoArguments.model_validate(arguments)
        async with self._sessionmaker() as session:
            _run, transaction = await self._load_context(session)
            if transaction is None:
                return AlertHistoryResult()
            alerts = await AlertEvidenceRepository(session, self._agency_id).for_transaction(
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
                transaction_id=transaction.id, alerts=items, status_aggregates=aggregates
            )

    async def _regulation_search(self, arguments: dict[str, JsonValue]) -> RegulationSearchResult:
        parsed_arguments = RegulationSearchArguments.model_validate(arguments)
        result = self._retriever.retrieve(parsed_arguments.query, top_k=parsed_arguments.top_k)
        matches = tuple(
            RegulationMatch(
                evidence_ref=f"regulation:{chunk.chunk_id}",
                chunk_id=chunk.chunk_id,
                document_id=chunk.doc_id,
                citation_id=chunk.citation,
                title=chunk.title,
                source=chunk.source,
                relevance_score=chunk.score,
            )
            for raw_chunk in result.chunks
            for chunk in (PersistedRegulationChunk.model_validate(raw_chunk),)
        )
        return RegulationSearchResult(
            mode=result.mode, rag_version=result.rag_version, matches=matches
        )
