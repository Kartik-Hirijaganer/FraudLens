"""Summary: The shared risk vocabulary every FraudLens package scores and reports against.

Key classes:
- RiskBand: ordinal risk classification for a scored transaction.

Key functions:
- (none)

Notes:
- `RiskBand` is deliberately the only type here. Release 0.5.0 removed the `TransactionSummary`
  placeholder that shipped alongside it: no production code ever read it, and a self-described
  sample model is exactly the kind of thing a reader mistakes for the real domain boundary. The
  real tenant-scoped transaction record is the persisted SQLAlchemy model plus the API schemas.
"""

from __future__ import annotations

from enum import StrEnum


class RiskBand(StrEnum):
    """Ordinal risk classification assigned to a scored transaction."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
