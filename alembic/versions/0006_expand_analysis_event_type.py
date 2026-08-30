"""Expand persisted analysis event names for multi-agent revision events.

Revision 0005 added ``agent.revision.requested`` to the application enum but left the
revision-0001 ``VARCHAR(22)`` column unchanged. The new value is 24 characters, so a real
reviewer-requested revision rolled back the run instead of persisting its replay event.

Revision ID: 0006_expand_analysis_event_type
Revises: 0005_add_agent_executions
Create Date: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_expand_analysis_event_type"
down_revision: str | None = "0005_add_agent_executions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PREVIOUS_LENGTH = 22
_MULTI_AGENT_LENGTH = 24


def upgrade() -> None:
    """Allow the longest persisted multi-agent event name."""
    with op.batch_alter_table("analysis_run_events", schema=None) as batch_op:
        batch_op.alter_column(
            "event_type",
            existing_type=sa.String(length=_PREVIOUS_LENGTH),
            type_=sa.String(length=_MULTI_AGENT_LENGTH),
            existing_nullable=False,
        )


def downgrade() -> None:
    """Restore the pre-agent event-name bound."""
    with op.batch_alter_table("analysis_run_events", schema=None) as batch_op:
        batch_op.alter_column(
            "event_type",
            existing_type=sa.String(length=_MULTI_AGENT_LENGTH),
            type_=sa.String(length=_PREVIOUS_LENGTH),
            existing_nullable=False,
        )
