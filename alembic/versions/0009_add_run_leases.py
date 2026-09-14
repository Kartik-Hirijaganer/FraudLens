"""Add durable investigation queue, lease, retry, and fencing state.

Revision ID: 0009_run_leases
Revises: 0008_transaction_source
Create Date: 2026-09-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_run_leases"
down_revision: str | None = "0008_transaction_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the worker queue fields without changing existing inline rows."""
    with op.batch_alter_table("analysis_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("request_fingerprint", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("model_override", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("lease_owner", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column("attempt", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column("fencing_token", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.create_index(
            "ix_analysis_runs_status_next_attempt_at",
            ["status", "next_attempt_at"],
            unique=False,
        )


def downgrade() -> None:
    """Remove durable execution state while retaining the original run record."""
    with op.batch_alter_table("analysis_runs", schema=None) as batch_op:
        batch_op.drop_index("ix_analysis_runs_status_next_attempt_at")
        batch_op.drop_column("fencing_token")
        batch_op.drop_column("deadline_at")
        batch_op.drop_column("next_attempt_at")
        batch_op.drop_column("attempt")
        batch_op.drop_column("heartbeat_at")
        batch_op.drop_column("lease_expires_at")
        batch_op.drop_column("lease_owner")
        batch_op.drop_column("model_override")
        batch_op.drop_column("request_fingerprint")
