"""Summary: Repository for the platform `agencies` table (plan §9.1). Unlike tenant
tables, `agencies` carries no `agency_id` (it is the tenant root), so it does not use the
tenant-scoped base; lookups are by primary key. `AgencyRepository.get` backs the
GET /api/v1/agencies/{agencyId} "tenant lookup (exists)" endpoint: it accepts the
agency id as a string or UUID, returns the `Agency` when present, and returns None for a
malformed id or a missing row (the route maps None → 404, with no existence leak).

Key classes:
- AgencyRepository: primary-key lookup for the platform `agencies` table.

Key functions:
- (none)

Notes:
- Tenant authorization (claim `agency_id` == requested) is enforced upstream in the route
  dependency; this repository only resolves existence and never widens tenant scope.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import Agency


class AgencyRepository:
    """Primary-key lookup for the platform `agencies` table (no tenant scoping)."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the async session used for lookups."""
        self._session = session

    async def get(self, agency_id: str | uuid.UUID) -> Agency | None:
        """Return the Agency for this id (str or UUID), or None if malformed/missing."""
        if isinstance(agency_id, uuid.UUID):
            key = agency_id
        else:
            try:
                key = uuid.UUID(str(agency_id))
            except ValueError:
                return None
        return await self._session.get(Agency, key)
