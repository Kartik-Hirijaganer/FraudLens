"""Add full tenant-scoped account digests; suffix masks cannot be safely backfilled.

Revision ID: 0015_account_identity_keys
Revises: 0014_upload_screening
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_account_identity_keys"
down_revision: str | None = "0014_upload_screening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable keys without claiming historical account or synthetic provenance."""
    for side in ("origin", "dest"):
        op.add_column(
            "transactions", sa.Column(f"{side}_account_key", sa.String(64), nullable=True)
        )
        op.create_index(
            f"ix_transactions_agency_id_{side}_key",
            "transactions",
            ["agency_id", f"{side}_account_key"],
        )


def downgrade() -> None:
    """Remove additive linkage keys only."""
    for side in ("dest", "origin"):
        op.drop_index(f"ix_transactions_agency_id_{side}_key", table_name="transactions")
        op.drop_column("transactions", f"{side}_account_key")
