"""Summary: Sample shared domain types for FraudLens. These are deliberately
minimal placeholders that demonstrate the Pydantic-everywhere convention (rule 1)
and tenant scoping; real AML/scoring models land in later feature plans.

Key classes:
- RiskBand: ordinal risk classification for a scored transaction.
- TransactionSummary: tenant-scoped summary of a single transaction.

Key functions:
- (none)

Notes:
- Every field carries Field(..., description=...) per the Pydantic-boundary rule.
- agency_id is present so the type is tenant-scoped from day one (FraudLens governance).
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RiskBand(StrEnum):
    """Ordinal risk classification assigned to a scored transaction."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TransactionSummary(BaseModel):
    """Tenant-scoped summary of a single transaction (sample domain model)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    transaction_id: str = Field(..., description="Opaque unique id of the transaction.")
    agency_id: str = Field(
        ...,
        description="Owning tenant (agency) id; every domain record is tenant-scoped.",
    )
    amount: Decimal = Field(..., ge=0, description="Transaction amount, non-negative.")
    currency: str = Field(
        ...,
        min_length=3,
        max_length=3,
        description="ISO-4217 three-letter currency code.",
    )
    risk_band: RiskBand = Field(
        default=RiskBand.LOW,
        description="Risk classification assigned by the scoring pipeline.",
    )
