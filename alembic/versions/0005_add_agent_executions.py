"""Add agent executions and multi-agent workflow provenance.

Creates the tenant-scoped ``agent_executions`` table, adds durable hashed
idempotency and workflow provenance to ``analysis_runs``, and records artifact-level
workflow/revision provenance on ``sar_drafts``. The Postgres DDL hardening trigger
installed by revision 0004 applies RLS/revokes to the new table automatically.

Revision ID: 0005_add_agent_executions
Revises: 0004_harden_supabase_access
Create Date: 2026-08-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005_add_agent_executions"
down_revision: str | None = "0004_harden_supabase_access"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine[object]:
    """Return JSONB on Postgres and generic JSON on SQLite."""
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    """Add durable agent/run/draft persistence without duplicating the RLS trigger."""
    with op.batch_alter_table("analysis_runs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "workflow_mode",
                sa.String(length=32),
                server_default="single_writer",
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("graph_version", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("idempotency_key", sa.String(length=64), nullable=True))
        batch_op.create_unique_constraint(
            "uq_analysis_runs_agency_id_idempotency_key",
            ["agency_id", "idempotency_key"],
        )

    with op.batch_alter_table("sar_drafts", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "workflow",
                sa.String(length=32),
                server_default="single_writer",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("revision_count", sa.Integer(), server_default="0", nullable=False)
        )

    op.create_table(
        "agent_executions",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column(
            "agent",
            sa.Enum(
                "evidence_investigator",
                "regulatory_analyst",
                "sar_writer",
                "compliance_reviewer",
                name="agentrole",
                native_enum=False,
                create_constraint=False,
            ),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=128), nullable=False),
        sa.Column("prompt_hash", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "completed",
                "degraded",
                "failed",
                name="agentexecutionstatus",
                native_enum=False,
                create_constraint=False,
            ),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("model_call_count", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("result", _json(), nullable=True),
        sa.Column("tool_calls", _json(), nullable=False),
        sa.Column("agency_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agency_id"], ["agencies.id"], name=op.f("fk_agent_executions_agency_id_agencies")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["analysis_runs.id"],
            name=op.f("fk_agent_executions_run_id_analysis_runs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_executions")),
        sa.UniqueConstraint(
            "run_id",
            "agent",
            "attempt",
            name="uq_agent_executions_run_id_agent_attempt",
        ),
    )
    with op.batch_alter_table("agent_executions", schema=None) as batch_op:
        batch_op.create_index(
            "ix_agent_executions_agency_id_run_id",
            ["agency_id", "run_id"],
            unique=False,
        )


def downgrade() -> None:
    """Remove the agent table and every provenance column added by this revision."""
    with op.batch_alter_table("agent_executions", schema=None) as batch_op:
        batch_op.drop_index("ix_agent_executions_agency_id_run_id")
    op.drop_table("agent_executions")

    with op.batch_alter_table("sar_drafts", schema=None) as batch_op:
        batch_op.drop_column("revision_count")
        batch_op.drop_column("workflow")

    with op.batch_alter_table("analysis_runs", schema=None) as batch_op:
        batch_op.drop_constraint("uq_analysis_runs_agency_id_idempotency_key", type_="unique")
        batch_op.drop_column("idempotency_key")
        batch_op.drop_column("graph_version")
        batch_op.drop_column("workflow_mode")
