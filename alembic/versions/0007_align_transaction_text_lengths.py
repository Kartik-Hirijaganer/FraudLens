"""Align persisted transaction text lengths with the public ingest contract.

The request boundary accepts 128-character account identifiers and channels, while the
initial schema persisted those fields as ``VARCHAR(64)``. Masking preserves identifier
length, so a valid request could otherwise fail with a database truncation error.

Revision ID: 0007_transaction_text_lengths
Revises: 0006_expand_analysis_event_type
Create Date: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_transaction_text_lengths"
down_revision: str | None = "0006_expand_analysis_event_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PREVIOUS_LENGTH = 64
_INGEST_LENGTH = 128
_COLUMNS = ("origin_account", "dest_account", "channel")


def upgrade() -> None:
    """Match persisted transaction fields to their validated request maximum."""
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        for column in _COLUMNS:
            batch_op.alter_column(
                column,
                existing_type=sa.String(length=_PREVIOUS_LENGTH),
                type_=sa.String(length=_INGEST_LENGTH),
                existing_nullable=False,
            )


def downgrade() -> None:
    """Restore the initial transaction field bounds."""
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        for column in _COLUMNS:
            batch_op.alter_column(
                column,
                existing_type=sa.String(length=_INGEST_LENGTH),
                type_=sa.String(length=_PREVIOUS_LENGTH),
                existing_nullable=False,
            )
