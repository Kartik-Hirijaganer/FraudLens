"""Async data-access repositories. `TenantScopedRepository` enforces `agency_id` isolation
for tenant tables; `AgencyRepository` resolves the platform `agencies` table. Re-exports
are intentional (see members)."""

from __future__ import annotations

from fraudlens_backend.db.repositories.agencies import AgencyRepository
from fraudlens_backend.db.repositories.base import TenantScopedRepository

__all__ = ["AgencyRepository", "TenantScopedRepository"]
