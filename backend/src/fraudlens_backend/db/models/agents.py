"""Summary: Tenant-scoped persistence for each bounded SAR-agent attempt. The
`agent_executions` table stores only prompt/input/result hashes, structured masked
results, bounded tool-call records, stable error codes, and usage/cost telemetry.

Key classes:
- AgentExecution: one immutable agent attempt tied to an analysis run.

Key functions:
- (none)

Notes:
- UNIQUE `(run_id, agent, attempt)` prevents duplicate replay charges and is the
  persistence seam used by the later resume phase.
- `result` and `tool_calls` are PHI-masked by `AgentExecutionRepository` before insert.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from fraudlens_backend.db.base import JSONB_TYPE, AgencyScopedMixin, Base, JsonValue, str_enum
from fraudlens_backend.db.models.enums import AgentExecutionStatus, AgentRole


class AgentExecution(AgencyScopedMixin, Base):
    """One immutable, tenant-scoped execution record for a bounded SAR agent."""

    __tablename__ = "agent_executions"
    __table_args__ = (
        Index("ix_agent_executions_agency_id_run_id", "agency_id", "run_id"),
        UniqueConstraint(
            "run_id", "agent", "attempt", name="uq_agent_executions_run_id_agent_attempt"
        ),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("analysis_runs.id"), nullable=False)
    agent: Mapped[AgentRole] = mapped_column(str_enum(AgentRole), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[AgentExecutionStatus] = mapped_column(
        str_enum(AgentExecutionStatus), nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    model_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0"))
    result: Mapped[JsonValue | None] = mapped_column(JSONB_TYPE, nullable=True)
    tool_calls: Mapped[list[JsonValue]] = mapped_column(JSONB_TYPE, nullable=False, default=list)
