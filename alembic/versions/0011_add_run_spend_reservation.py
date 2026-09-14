"""Persist worst-case LLM spend reservations on active investigation runs.

Revision ID: 0011_run_spend_reserve
Revises: 0010_run_stage_unique
Create Date: 2026-09-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011_run_spend_reserve"
down_revision: str | None = "0010_run_stage_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add a retained audit value used while a run is non-terminal."""
    with op.batch_alter_table("analysis_runs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "llm_reserved_usd",
                sa.Numeric(precision=12, scale=6),
                server_default="0",
                nullable=False,
            )
        )


def downgrade() -> None:
    """Remove the durable reservation audit value."""
    with op.batch_alter_table("analysis_runs", schema=None) as batch_op:
        batch_op.drop_column("llm_reserved_usd")
