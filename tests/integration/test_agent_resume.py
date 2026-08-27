"""Summary: Integration coverage for Phase 7 agent resume-by-replay.

Key classes:
- _ReplayRuntime: deterministic non-provider executor with call and cost accounting.

Key functions:
- (none)

Notes:
- SQLite exercises the same keyed local serialization used beside PostgreSQL advisory locks.
- Every row and replay lookup remains scoped by the seeded agency and analysis run.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.agents.config import AgentRole, AgentsConfig, load_agents_config
from fraudlens_backend.agents.contracts import (
    AgentExecutionRecord,
    AgentExecutionStatus,
    AgentToolCallRecord,
    AgentToolCallStatus,
    EvidenceBrief,
    EvidenceFinding,
    RegulatoryBrief,
    RegulatoryFinding,
    ReviewDecision,
    ReviewVerdict,
)
from fraudlens_backend.agents.graph import AgentGraph, build_agent_graph
from fraudlens_backend.agents.prompts import AgentPromptTemplate
from fraudlens_backend.agents.resume import (
    AgentExecutionReplay,
    _acquire_postgres_advisory_lock,
    _advisory_lock_key,
)
from fraudlens_backend.agents.runtime import agent_input_hash
from fraudlens_backend.agents.tools import AGENT_TOOL_NAMES
from fraudlens_backend.db.models import Agency, AgentExecution, AnalysisRun, RunStatus, Transaction
from fraudlens_backend.db.repositories import AgentExecutionRepository
from fraudlens_backend.settings import find_config_dir
from fraudlens_llm import load_catalog
from fraudlens_ml.sar import SarClaim, SarDraftContent, SarInput, SarStreamEvent

_ATTEMPT_COST = Decimal("0.001000")


class _ReplayRuntime:
    """Return deterministic structured records while exposing every actual role invocation."""

    def __init__(
        self,
        config: AgentsConfig,
        *,
        fail_before: AgentRole | None = None,
        delay_s: float = 0,
    ) -> None:
        """Bind config plus optional interruption and concurrency delay behavior."""
        self._config = config
        self._fail_before = fail_before
        self._failed = False
        self._delay_s = delay_s
        self.calls: list[tuple[AgentRole, int]] = []

    async def execute(
        self,
        *,
        agent: AgentRole,
        prompt: AgentPromptTemplate,
        user_content: str,
        response_model: type[BaseModel],
        attempt: int = 1,
    ) -> AgentExecutionRecord:
        """Produce one canonical record or simulate termination before provider access."""
        _ = response_model
        if agent is self._fail_before and not self._failed:
            self._failed = True
            raise RuntimeError("synthetic process interruption")
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        self.calls.append((agent, attempt))
        result = _result_for(agent)
        payload = result.model_dump(mode="json", by_alias=True)
        tool_calls = (
            (
                AgentToolCallRecord(
                    call_id="resume-evidence-1",
                    name="rule_hits",
                    status=AgentToolCallStatus.COMPLETED,
                    result={"hits": [{"evidenceRef": "rule-hit:synthetic:0"}]},
                ),
            )
            if agent is AgentRole.EVIDENCE_INVESTIGATOR
            else ()
        )
        return AgentExecutionRecord(
            agent=agent,
            attempt=attempt,
            status=AgentExecutionStatus.COMPLETED,
            model_id=self._config.agents.for_role(agent).model,
            prompt_version=prompt.prompt_version,
            prompt_hash=prompt.prompt_hash,
            input_hash=agent_input_hash(
                agent=agent,
                prompt=prompt,
                user_content=user_content,
            ),
            result_hash=_hash_json(payload),
            latency_ms=7,
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            cost_usd=_ATTEMPT_COST,
            result=payload,
            tool_calls=tool_calls,
        )


def _result_for(agent: AgentRole) -> BaseModel:
    """Return the valid structured response associated with one workflow role."""
    if agent is AgentRole.EVIDENCE_INVESTIGATOR:
        return EvidenceBrief(
            summary="Persisted evidence supports human review.",
            findings=(
                EvidenceFinding(
                    statement="A deterministic rule identified a notable pattern.",
                    evidence_refs=("rule-hit:synthetic:0",),
                ),
            ),
        )
    if agent is AgentRole.REGULATORY_ANALYST:
        return RegulatoryBrief(
            summary="The persisted provision may apply.",
            findings=(
                RegulatoryFinding(
                    citation_id="31 CFR 1010.314",
                    title="Structuring transactions",
                    application="The supplied pattern warrants human review.",
                ),
            ),
        )
    if agent is AgentRole.SAR_WRITER:
        return SarDraftContent(
            subject="Synthetic activity review",
            narrative="A persisted pattern warrants human review.",
            recommended_action="Escalate for human review.",
            cited_regulations=("31 CFR 1010.314",),
            claims=(
                SarClaim(
                    statement="A persisted pattern warrants human review.",
                    evidence_refs=("rule-hit:synthetic:0",),
                    citation_ids=("31 CFR 1010.314",),
                ),
            ),
        )
    return ReviewVerdict(decision=ReviewDecision.PASS)


def _config_and_prompts() -> tuple[AgentsConfig, dict[AgentRole, AgentPromptTemplate]]:
    """Load the committed graph config and exact prompt provenance."""
    config = load_agents_config(
        catalog=load_catalog(find_config_dir() / "llm" / "catalog.yml"),
        available_tools=AGENT_TOOL_NAMES,
    )
    return config, {
        role: AgentPromptTemplate.load(role, config.agents.for_role(role).prompt_id)
        for role in AgentRole
    }


async def _seed_run(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Seed one tenant, transaction, and running multi-agent analysis."""
    agency_id = uuid.uuid4()
    async with sessionmaker() as session:
        session.add(
            Agency(
                id=agency_id,
                name="Resume test tenant",
                slug=f"resume-{agency_id.hex}",
            )
        )
        transaction = Transaction(
            agency_id=agency_id,
            external_id="resume-transaction",
            amount=Decimal("9500.00"),
            currency="USD",
            occurred_at=datetime(2026, 8, 17, tzinfo=UTC),
            origin_account="****1111",
            dest_account="****2222",
            channel="wire",
            country="US",
            features={},
            feature_hash="f" * 64,
        )
        session.add(transaction)
        await session.flush()
        run = AnalysisRun(
            agency_id=agency_id,
            transaction_id=transaction.id,
            status=RunStatus.RUNNING,
            workflow_mode="multi_agent",
            graph_version="agents-v1",
        )
        session.add(run)
        await session.commit()
        return agency_id, transaction.id, run.id


def _recorder(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    agency_id: uuid.UUID,
    run_id: uuid.UUID,
) -> Callable[[AgentExecutionRecord], Awaitable[None]]:
    """Return a short-transaction upsert callback safe for parallel graph nodes."""
    write_lock = asyncio.Lock()

    async def record(item: AgentExecutionRecord) -> None:
        async with write_lock, sessionmaker() as session:
            await AgentExecutionRepository(session, agency_id).save_from_record(
                run_id=run_id,
                record=item,
            )
            await session.commit()

    return record


def _graph(
    *,
    runtime: _ReplayRuntime,
    config: AgentsConfig,
    prompts: dict[AgentRole, AgentPromptTemplate],
    replay: AgentExecutionReplay,
    record: Callable[[AgentExecutionRecord], Awaitable[None]],
    run_id: uuid.UUID,
) -> AgentGraph:
    """Build one fresh process-local graph over shared persistent replay state."""
    return build_agent_graph(
        runtime=runtime,
        config=config,
        prompts=prompts,
        run_id=run_id,
        record_execution=record,
        replay=replay,
    )


async def _stored_cost(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    agency_id: uuid.UUID,
    run_id: uuid.UUID,
) -> tuple[int, Decimal]:
    """Return tenant-scoped persisted attempt count and aggregate cost."""
    async with sessionmaker() as session:
        count, cost = (
            await session.execute(
                select(func.count(), func.sum(AgentExecution.cost_usd)).where(
                    AgentExecution.agency_id == agency_id,
                    AgentExecution.run_id == run_id,
                )
            )
        ).one()
    return int(count), Decimal(cost or 0)


async def test_restart_replays_completed_agents_without_duplicate_cost(
    db_sessionmaker: async_sessionmaker[AsyncSession],
    make_sar_input: Callable[..., SarInput],
) -> None:
    """A fresh graph resumes after interruption without reinvoking completed roles."""
    agency_id, transaction_id, run_id = await _seed_run(db_sessionmaker)
    config, prompts = _config_and_prompts()
    record = _recorder(db_sessionmaker, agency_id=agency_id, run_id=run_id)
    first_runtime = _ReplayRuntime(config, fail_before=AgentRole.SAR_WRITER)
    first_graph = _graph(
        runtime=first_runtime,
        config=config,
        prompts=prompts,
        replay=AgentExecutionReplay(db_sessionmaker, agency_id=agency_id, run_id=run_id),
        record=record,
        run_id=run_id,
    )
    sar_input = make_sar_input(
        agency_id=str(agency_id),
        transaction_id=str(transaction_id),
    )

    with pytest.raises(RuntimeError, match="synthetic process interruption"):
        await first_graph.run(sar_input, emit=_ignore_event)

    second_runtime = _ReplayRuntime(config)
    result = await _graph(
        runtime=second_runtime,
        config=config,
        prompts=prompts,
        replay=AgentExecutionReplay(db_sessionmaker, agency_id=agency_id, run_id=run_id),
        record=record,
        run_id=run_id,
    ).run(sar_input, emit=_ignore_event)

    assert set(first_runtime.calls) == {
        (AgentRole.EVIDENCE_INVESTIGATOR, 1),
        (AgentRole.REGULATORY_ANALYST, 1),
    }
    assert second_runtime.calls == [
        (AgentRole.SAR_WRITER, 1),
        (AgentRole.COMPLIANCE_REVIEWER, 1),
    ]
    assert len(result.executions) == 4
    assert await _stored_cost(
        db_sessionmaker,
        agency_id=agency_id,
        run_id=run_id,
    ) == (4, _ATTEMPT_COST * 4)


async def test_concurrent_resumers_execute_each_node_once(
    db_sessionmaker: async_sessionmaker[AsyncSession],
    make_sar_input: Callable[..., SarInput],
) -> None:
    """Two fresh resumptions share one run lock and never duplicate an agent charge."""
    agency_id, transaction_id, run_id = await _seed_run(db_sessionmaker)
    config, prompts = _config_and_prompts()
    runtime = _ReplayRuntime(config, delay_s=0.01)
    record = _recorder(db_sessionmaker, agency_id=agency_id, run_id=run_id)
    sar_input = make_sar_input(
        agency_id=str(agency_id),
        transaction_id=str(transaction_id),
    )
    graphs = [
        _graph(
            runtime=runtime,
            config=config,
            prompts=prompts,
            replay=AgentExecutionReplay(
                db_sessionmaker,
                agency_id=agency_id,
                run_id=run_id,
            ),
            record=record,
            run_id=run_id,
        )
        for _index in range(2)
    ]

    results = await asyncio.gather(*(graph.run(sar_input, emit=_ignore_event) for graph in graphs))

    assert Counter(runtime.calls) == Counter(
        {
            (AgentRole.EVIDENCE_INVESTIGATOR, 1): 1,
            (AgentRole.REGULATORY_ANALYST, 1): 1,
            (AgentRole.SAR_WRITER, 1): 1,
            (AgentRole.COMPLIANCE_REVIEWER, 1): 1,
        }
    )
    assert all(len(result.executions) == 4 for result in results)
    assert await _stored_cost(
        db_sessionmaker,
        agency_id=agency_id,
        run_id=run_id,
    ) == (4, _ATTEMPT_COST * 4)


async def test_changed_input_hash_reruns_and_replaces_only_that_attempt(
    db_sessionmaker: async_sessionmaker[AsyncSession],
    make_sar_input: Callable[..., SarInput],
) -> None:
    """A stale hash is never replayed and its unique logical attempt is safely replaced."""
    agency_id, transaction_id, run_id = await _seed_run(db_sessionmaker)
    config, prompts = _config_and_prompts()
    record = _recorder(db_sessionmaker, agency_id=agency_id, run_id=run_id)
    sar_input = make_sar_input(
        agency_id=str(agency_id),
        transaction_id=str(transaction_id),
    )
    first_runtime = _ReplayRuntime(config)
    await _graph(
        runtime=first_runtime,
        config=config,
        prompts=prompts,
        replay=AgentExecutionReplay(db_sessionmaker, agency_id=agency_id, run_id=run_id),
        record=record,
        run_id=run_id,
    ).run(sar_input, emit=_ignore_event)
    async with db_sessionmaker() as session:
        row = (
            await session.execute(
                select(AgentExecution).where(
                    AgentExecution.agency_id == agency_id,
                    AgentExecution.run_id == run_id,
                    AgentExecution.agent == AgentRole.EVIDENCE_INVESTIGATOR,
                    AgentExecution.attempt == 1,
                )
            )
        ).scalar_one()
        row.input_hash = "stale-input-hash"
        await session.commit()

    second_runtime = _ReplayRuntime(config)
    await _graph(
        runtime=second_runtime,
        config=config,
        prompts=prompts,
        replay=AgentExecutionReplay(db_sessionmaker, agency_id=agency_id, run_id=run_id),
        record=record,
        run_id=run_id,
    ).run(sar_input, emit=_ignore_event)

    assert second_runtime.calls == [(AgentRole.EVIDENCE_INVESTIGATOR, 1)]
    assert await _stored_cost(
        db_sessionmaker,
        agency_id=agency_id,
        run_id=run_id,
    ) == (4, _ATTEMPT_COST * 4)


async def test_postgres_advisory_lock_binds_the_tenant_run_tuple() -> None:
    """The production lock seam issues one transaction lock with a stable tuple-derived key."""
    agency_id = uuid.uuid4()
    run_id = uuid.uuid4()
    session = AsyncMock()

    await _acquire_postgres_advisory_lock(
        cast(AsyncSession, session),
        agency_id,
        run_id,
    )

    statement, params = session.execute.await_args.args
    assert str(statement) == "SELECT pg_advisory_xact_lock(:lock_key)"
    assert params == {"lock_key": _advisory_lock_key(agency_id, run_id)}
    assert params["lock_key"] != _advisory_lock_key(agency_id, uuid.uuid4())


async def _ignore_event(_event: SarStreamEvent) -> None:
    """Consume a lifecycle event without adding test-side timing behavior."""


def _hash_json(value: object) -> str:
    """Hash one deterministic structured test result."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
