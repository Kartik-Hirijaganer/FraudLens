"""Summary: Shared camelCase API projection for persisted SAR-agent execution attempts.
The view is reused by investigation snapshots and alert detail so both surfaces expose the same
PHI-masked trace, stable attempt identity, provenance, tool audit, latency, usage, and cost.

Key classes:
- AgentExecutionView: one persisted agent attempt on the API surface.

Key functions:
- agent_execution_to_view: map the tenant-scoped ORM row to its shared API projection.

Notes:
- Structured result and tool data were recursively masked before persistence.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from fraudlens_backend.agents.contracts import agent_run_id
from fraudlens_backend.db.models import AgentExecution
from fraudlens_backend.db.repositories.agents import agent_execution_to_record
from fraudlens_backend.models.common import CamelModel


class AgentExecutionView(CamelModel):
    """One normalized, PHI-masked agent execution attempt."""

    agent_run_id: str = Field(..., description="Stable identity for this role attempt.")
    agent: str = Field(..., description="Configured agent role.")
    attempt: int = Field(..., ge=1, description="One-based role attempt.")
    status: str = Field(..., description="Completed, degraded, or failed outcome.")
    error_code: str | None = Field(default=None, description="Stable failure/degradation code.")
    model_id: str = Field(..., description="Catalog model reference that served this attempt.")
    prompt_version: str = Field(..., description="Versioned prompt identifier.")
    prompt_hash: str = Field(..., description="Hash of the exact prompt template.")
    input_hash: str = Field(..., description="Hash of the canonical agent input.")
    result_hash: str | None = Field(default=None, description="Hash of the structured result.")
    latency_ms: int = Field(..., ge=0, description="Wall-clock execution duration.")
    model_call_count: int = Field(
        ..., ge=0, description="Successful provider generations completed in this attempt."
    )
    input_tokens: int = Field(..., ge=0, description="Provider input tokens.")
    output_tokens: int = Field(..., ge=0, description="Provider output tokens.")
    total_tokens: int = Field(..., ge=0, description="Provider total tokens.")
    cost_usd: str = Field(..., description="Exact USD cost as a NUMERIC string.")
    result: dict[str, Any] | None = Field(
        default=None, description="Validated, PHI-masked structured result."
    )
    tool_calls: list[dict[str, Any]] = Field(
        default_factory=list, description="Ordered, PHI-masked tool invocation records."
    )


def agent_execution_to_view(row: AgentExecution) -> AgentExecutionView:
    """Map one tenant-scoped persisted attempt onto the shared API view."""
    record = agent_execution_to_record(row)
    return AgentExecutionView.model_validate(
        {
            "agent_run_id": agent_run_id(row.run_id, record.agent, record.attempt),
            **record.model_dump(mode="json", exclude={"guardrail_decision"}),
        }
    )
