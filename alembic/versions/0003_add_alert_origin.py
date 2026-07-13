"""Summary: Add explicit provenance to alerts so seeded sample rows cannot be mistaken
for alerts raised by a real investigation pipeline. Existing rows are backfilled as
`pipeline`; the demo seed writes `seed` explicitly after this migration lands.

Key classes:
- (none)

Key functions:
- upgrade: add the constrained, non-null alert origin with a pipeline server default.
- downgrade: remove the alert origin column and its portable check constraint.

Notes:
- The string enum is portable across PostgreSQL and SQLite and stores lowercase values.
- The server default preserves compatibility for existing and non-ORM alert writers.

Revision ID: 0003_add_alert_origin
Revises: 0002_extend_alert_statuses
Create Date: 2026-07-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_add_alert_origin"
down_revision: str | None = "0002_extend_alert_statuses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ORIGINS = ("pipeline", "seed")


def _alert_origin() -> sa.Enum:
    """Return the portable alert-origin enum/check column type."""
    return sa.Enum(
        *_ORIGINS,
        name="alertorigin",
        native_enum=False,
        create_constraint=True,
    )


def upgrade() -> None:
    """Add origin and classify all pre-existing alerts as pipeline-generated."""
    with op.batch_alter_table("alerts", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "origin",
                _alert_origin(),
                server_default=sa.text("'pipeline'"),
                nullable=False,
            )
        )


def downgrade() -> None:
    """Remove alert provenance and its check constraint."""
    with op.batch_alter_table("alerts", schema=None) as batch_op:
        batch_op.drop_constraint(op.f("ck_alerts_alertorigin"), type_="check")
        batch_op.drop_column("origin", existing_type=_alert_origin())
