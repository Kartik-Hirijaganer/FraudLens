"""Summary: Extend the alert lifecycle status check with Phase 2 review buckets.

Adds `pending_review` and `escalated` to the string-stored `alertstatus` check on
`alerts.status`. The values are additive on upgrade. Downgrade maps the additive states
back to the closest pre-Phase-2 lifecycle states before restoring the old check.

Revision ID: 0002_extend_alert_statuses
Revises: 0001_initial_schema
Create Date: 2026-06-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_extend_alert_statuses"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_STATUSES = ("open", "in_review", "resolved", "dismissed")
_NEW_STATUSES = ("open", "pending_review", "in_review", "escalated", "resolved", "dismissed")


def _alert_status(*values: str, create_constraint: bool) -> sa.Enum:
    """Return the portable alertstatus enum/check column type."""
    return sa.Enum(
        *values,
        name="alertstatus",
        native_enum=False,
        create_constraint=create_constraint,
    )


def upgrade() -> None:
    """Expand `alerts.status` to admit pending review and escalated rows."""
    with op.batch_alter_table("alerts", schema=None) as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=_alert_status(*_OLD_STATUSES, create_constraint=False),
            type_=_alert_status(*_NEW_STATUSES, create_constraint=True),
            existing_nullable=False,
        )


def downgrade() -> None:
    """Restore the pre-Phase-2 alertstatus check after normalizing additive states."""
    op.execute("UPDATE alerts SET status = 'open' WHERE status = 'pending_review'")
    op.execute("UPDATE alerts SET status = 'in_review' WHERE status = 'escalated'")
    with op.batch_alter_table("alerts", schema=None) as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=_alert_status(*_NEW_STATUSES, create_constraint=True),
            type_=_alert_status(*_OLD_STATUSES, create_constraint=True),
            existing_nullable=False,
        )
