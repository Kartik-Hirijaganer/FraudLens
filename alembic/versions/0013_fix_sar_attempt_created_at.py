"""Restore the missing `created_at` default on `sar_generation_attempts`.

Revision 0012 created the table with ``created_at`` NOT NULL and no server default, while the
ORM's ``CreatedAtMixin`` declares ``server_default=func.now()``. SQLAlchemy relies on that
default and does not send the column on INSERT, so every write to the table failed in Postgres
with SQLSTATE 23502 — the table has never been insertable there. Because the cascade records one
row per generation stage, that took down every live investigation whose SAR reached persistence.

The tests never caught it: they build the schema with ``Base.metadata.create_all`` from the
models, which carry the default, so CI validated a schema Alembic had never produced. Only
production runs the migrations.

Revision ID: 0013_sar_attempt_created_at
Revises: 0012_sar_quality_gate
Create Date: 2026-09-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013_sar_attempt_created_at"
down_revision: str | None = "0012_sar_quality_gate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "sar_generation_attempts"
_COLUMN = "created_at"
# The literal every other table in this schema uses for the same column (revision 0001).
_DEFAULT = "(CURRENT_TIMESTAMP)"


def upgrade() -> None:
    """Attach the server default the model has always declared for this column.

    Batch mode because SQLite has no `ALTER COLUMN ... SET DEFAULT`: Alembic emits a plain ALTER
    on Postgres and recreates the table on SQLite, which is the pattern the rest of this history
    uses for the same reason.
    """
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        batch_op.alter_column(
            _COLUMN,
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
            server_default=sa.text(_DEFAULT),
        )


def downgrade() -> None:
    """Drop the default again, returning the column to its 0012 shape."""
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        batch_op.alter_column(
            _COLUMN,
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
            server_default=None,
        )
