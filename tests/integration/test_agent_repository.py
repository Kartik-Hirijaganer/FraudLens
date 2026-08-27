"""Summary: Integration coverage for Phase 5 agent-execution persistence.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- The tests prove recursive PHI masking, tenant-scoped reads, and duplicate-attempt rejection.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.agents import (
    AgentExecutionRecord,
    AgentExecutionStatus,
    AgentRole,
    AgentToolCallRecord,
    AgentToolCallStatus,
)
from fraudlens_backend.db.repositories import AgentExecutionRepository


def _record(*, attempt: int = 1) -> AgentExecutionRecord:
    """Return a complete execution record containing deliberately maskable values."""
    return AgentExecutionRecord(
        agent=AgentRole.EVIDENCE_INVESTIGATOR,
        attempt=attempt,
        status=AgentExecutionStatus.COMPLETED,
        model_id="openrouter/test/model",
        prompt_version="v1",
        prompt_hash="a" * 64,
        input_hash="b" * 64,
        result_hash="c" * 64,
        latency_ms=17,
        model_call_count=3,
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        cost_usd=Decimal("0.000123"),
        result={"summary": "Contact analyst@example.test", "claimIds": ["claim-1"]},
        tool_calls=(
            AgentToolCallRecord(
                call_id="call-1",
                name="transaction_history",
                arguments={"query": "123-45-6789"},
                status=AgentToolCallStatus.COMPLETED,
                result={"account": "123456789012"},
            ),
        ),
    )


async def test_create_masks_structured_payload_and_scopes_reads(
    db_session: AsyncSession,
) -> None:
    owner = uuid.uuid4()
    other = uuid.uuid4()
    run_id = uuid.uuid4()
    row = await AgentExecutionRepository(db_session, owner).create_from_record(
        run_id=run_id,
        record=_record(),
    )

    assert row.result == {
        "summary": "Contact [REDACTED_EMAIL]",
        "claimIds": ["claim-1"],
    }
    assert row.tool_calls[0]["arguments"]["query"] == "[REDACTED_SSN]"
    assert row.tool_calls[0]["result"]["account"] == "[REDACTED_BANK_ACCOUNT]"
    assert row.agent is AgentRole.EVIDENCE_INVESTIGATOR
    assert row.status is AgentExecutionStatus.COMPLETED
    assert row.model_call_count == 3
    assert row.cost_usd == Decimal("0.000123")
    assert await AgentExecutionRepository(db_session, other).list_for_run(run_id) == []
    assert await AgentExecutionRepository(db_session, other).list_completed_for_run(run_id) == []
    assert await AgentExecutionRepository(db_session, owner).list_completed_for_run(run_id) == [row]
    assert (
        await AgentExecutionRepository(db_session, owner).get_attempt(
            run_id=run_id,
            agent=AgentRole.EVIDENCE_INVESTIGATOR,
            attempt=1,
        )
        is row
    )


async def test_duplicate_run_agent_attempt_conflicts(db_session: AsyncSession) -> None:
    repo = AgentExecutionRepository(db_session, uuid.uuid4())
    run_id = uuid.uuid4()
    await repo.create_from_record(run_id=run_id, record=_record())

    with pytest.raises(IntegrityError):
        await repo.create_from_record(run_id=run_id, record=_record())


async def test_save_replaces_stale_attempt_without_duplicating_cost(
    db_session: AsyncSession,
) -> None:
    """Resume persistence updates one logical attempt instead of inserting a second charge."""
    repo = AgentExecutionRepository(db_session, uuid.uuid4())
    run_id = uuid.uuid4()
    original = await repo.save_from_record(run_id=run_id, record=_record())
    refreshed = await repo.save_from_record(
        run_id=run_id,
        record=_record().model_copy(
            update={
                "input_hash": "d" * 64,
                "cost_usd": Decimal("0.000456"),
            }
        ),
    )

    assert refreshed is original
    assert refreshed.input_hash == "d" * 64
    assert refreshed.cost_usd == Decimal("0.000456")
    assert len(await repo.list_for_run(run_id)) == 1
