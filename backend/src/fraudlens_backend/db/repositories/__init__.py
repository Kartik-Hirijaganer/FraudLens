"""Async data-access repositories. `TenantScopedRepository` enforces `agency_id` isolation
for tenant tables; `AgencyRepository` resolves the platform `agencies` table;
`RuleRepository` does agency-scoped CRUD over `aml_rules` (nullable agency_id) + loads the
merged engine rule set; `ModelRegistryRepository` reads the platform model registry + resolves
the active deployment pointer. Re-exports are intentional (see members)."""

from __future__ import annotations

from fraudlens_backend.db.repositories.agencies import AgencyRepository
from fraudlens_backend.db.repositories.base import TenantScopedRepository
from fraudlens_backend.db.repositories.model_registry import ModelRegistryRepository
from fraudlens_backend.db.repositories.rules import RuleRepository
from fraudlens_backend.db.repositories.transactions import (
    IngestOutcome,
    TransactionRepository,
)

__all__ = [
    "AgencyRepository",
    "IngestOutcome",
    "ModelRegistryRepository",
    "RuleRepository",
    "TenantScopedRepository",
    "TransactionRepository",
]
