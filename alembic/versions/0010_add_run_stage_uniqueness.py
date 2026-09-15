"""Enforce restart-safe uniqueness for durable investigation stage outputs.

Revision ID: 0010_run_stage_unique
Revises: 0009_run_leases
Create Date: 2026-09-14
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010_run_stage_unique"
down_revision: str | None = "0009_run_leases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Prevent concurrent recovery from duplicating run-owned stage records."""
    with op.batch_alter_table("model_inference_logs", schema=None) as batch_op:
        batch_op.create_unique_constraint("uq_model_inference_logs_run_id", ["run_id"])
    with op.batch_alter_table("alerts", schema=None) as batch_op:
        batch_op.create_unique_constraint("uq_alerts_run_id", ["run_id"])
    with op.batch_alter_table("sar_drafts", schema=None) as batch_op:
        batch_op.create_unique_constraint("uq_sar_drafts_run_id_version", ["run_id", "version"])


def downgrade() -> None:
    """Remove the additional run-stage uniqueness constraints."""
    with op.batch_alter_table("sar_drafts", schema=None) as batch_op:
        batch_op.drop_constraint("uq_sar_drafts_run_id_version", type_="unique")
    with op.batch_alter_table("alerts", schema=None) as batch_op:
        batch_op.drop_constraint("uq_alerts_run_id", type_="unique")
    with op.batch_alter_table("model_inference_logs", schema=None) as batch_op:
        batch_op.drop_constraint("uq_model_inference_logs_run_id", type_="unique")
