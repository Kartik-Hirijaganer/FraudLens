"""Summary: Tenant-scoped persistence and replay lookups for bounded SAR-agent
attempts. The repository projects typed `AgentExecutionRecord` values onto the
`agent_executions` table and masks every string leaf in structured result/tool data
before it reaches JSONB.

Key classes:
- AgentExecutionRepository: agency-scoped persistence and replay operations for agent attempts.

Key functions:
- agent_execution_to_record: rebuild the typed runtime contract from a persisted attempt.

Notes:
- Tool arguments and results are masked again at persistence as defense in depth; the
  runtime's guardrails remain the first masking boundary.
- Reads always include `agency_id`, including the `(run_id, agent, attempt)` replay lookup.
- A stale or non-completed logical attempt is replaced under the Phase 7 run lock.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import AgentExecution, AgentExecutionStatus
from fraudlens_backend.db.repositories.base import TenantScopedRepository
from fraudlens_core.phi import mask_text

if TYPE_CHECKING:
    from fraudlens_backend.agents.config import AgentRole
    from fraudlens_backend.agents.contracts import AgentExecutionRecord


class AgentExecutionRepository(TenantScopedRepository[AgentExecution]):
    """Agency-scoped persistence and replay lookups for agent execution records."""

    def __init__(self, session: AsyncSession, agency_id: uuid.UUID) -> None:
        """Bind the session and verified agency scope to `agent_executions`."""
        super().__init__(session, AgentExecution, agency_id)

    async def create_from_record(
        self, *, run_id: uuid.UUID, record: AgentExecutionRecord
    ) -> AgentExecution:
        """Persist one typed attempt after recursively masking its structured JSON."""
        execution = AgentExecution(
            agency_id=self._agency_id,
            run_id=run_id,
        )
        _apply_record(execution, record)
        self._session.add(execution)
        await self._session.flush()
        return execution

    async def save_from_record(
        self, *, run_id: uuid.UUID, record: AgentExecutionRecord
    ) -> AgentExecution:
        """Insert an attempt or replace its stale/non-completed state under the run lock."""
        execution = await self.get_attempt(
            run_id=run_id,
            agent=record.agent,
            attempt=record.attempt,
        )
        if execution is None:
            return await self.create_from_record(run_id=run_id, record=record)
        _apply_record(execution, record)
        await self._session.flush()
        return execution

    async def get_attempt(
        self, *, run_id: uuid.UUID, agent: AgentRole, attempt: int
    ) -> AgentExecution | None:
        """Return one attempt only when it belongs to this agency and run."""
        stmt = select(AgentExecution).where(
            AgentExecution.agency_id == self._agency_id,
            AgentExecution.run_id == run_id,
            AgentExecution.agent == agent,
            AgentExecution.attempt == attempt,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_for_run(self, run_id: uuid.UUID) -> Sequence[AgentExecution]:
        """Return attempts in workflow order, with revisions following their first attempts."""
        role_order = case(
            {
                "evidence_investigator": 1,
                "regulatory_analyst": 2,
                "sar_writer": 3,
                "compliance_reviewer": 4,
            },
            value=AgentExecution.agent,
            else_=5,
        )
        stmt = (
            select(AgentExecution)
            .where(
                AgentExecution.agency_id == self._agency_id,
                AgentExecution.run_id == run_id,
            )
            .order_by(AgentExecution.attempt.asc(), role_order.asc())
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def list_completed_for_run(self, run_id: uuid.UUID) -> Sequence[AgentExecution]:
        """Return replayable completed attempts for one tenant-scoped analysis run."""
        stmt = (
            select(AgentExecution)
            .where(
                AgentExecution.agency_id == self._agency_id,
                AgentExecution.run_id == run_id,
                AgentExecution.status == AgentExecutionStatus.COMPLETED,
            )
            .order_by(AgentExecution.attempt.asc(), AgentExecution.agent.asc())
        )
        return (await self._session.execute(stmt)).scalars().all()


def agent_execution_to_record(row: AgentExecution) -> AgentExecutionRecord:
    """Rebuild the typed runtime contract from one validated persistence row."""
    from fraudlens_backend.agents.contracts import (  # noqa: PLC0415 - avoids package cycle.
        AgentExecutionRecord,
        AgentToolCallRecord,
    )

    if row.result is not None and not isinstance(row.result, dict):
        raise TypeError("Agent execution result must be an object")
    if not isinstance(row.tool_calls, list):
        raise TypeError("Agent execution tool calls must be a list")
    return AgentExecutionRecord(
        agent=row.agent,
        attempt=row.attempt,
        status=row.status,
        error_code=row.error_code,
        model_id=row.model_id,
        prompt_version=row.prompt_version,
        prompt_hash=row.prompt_hash,
        input_hash=row.input_hash,
        result_hash=row.result_hash,
        latency_ms=row.latency_ms,
        model_call_count=row.model_call_count,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        total_tokens=row.total_tokens,
        cost_usd=row.cost_usd,
        result=row.result,
        tool_calls=tuple(AgentToolCallRecord.model_validate(item) for item in row.tool_calls),
    )


def _mask_json(value: Any) -> Any:
    """Recursively mask string leaves while preserving the input's JSON shape."""
    if isinstance(value, str):
        return mask_text(value).value
    if isinstance(value, Mapping):
        return {str(key): _mask_json(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_mask_json(item) for item in value]
    return value


def _apply_record(execution: AgentExecution, record: AgentExecutionRecord) -> None:
    """Copy one typed, recursively masked attempt onto a new or existing ORM row."""
    execution.agent = record.agent
    execution.attempt = record.attempt
    execution.model_id = record.model_id
    execution.prompt_version = record.prompt_version
    execution.prompt_hash = record.prompt_hash
    execution.input_hash = record.input_hash
    execution.result_hash = record.result_hash
    execution.status = record.status
    execution.error_code = record.error_code
    execution.latency_ms = record.latency_ms
    execution.model_call_count = record.model_call_count
    execution.input_tokens = record.input_tokens
    execution.output_tokens = record.output_tokens
    execution.total_tokens = record.total_tokens
    execution.cost_usd = record.cost_usd
    execution.result = _mask_json(record.result) if record.result is not None else None
    execution.tool_calls = [
        _mask_json(item.model_dump(mode="json", by_alias=True)) for item in record.tool_calls
    ]
