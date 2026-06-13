"""Summary: The tenant-scoped repository base (plan §6.4 / §9). `TenantScopedRepository`
binds an `AsyncSession`, a tenant-scoped model type, and a single `agency_id` at
construction, then enforces that scope on every operation: reads filter by `agency_id`
(so a cross-agency id resolves to nothing, never another tenant's row) and writes stamp
the row's `agency_id` to the repository's scope. This centralizes the
`fraudlens_core.require_agency_id` invariant at the data layer so feature repositories
(Phase 3+) inherit isolation by construction rather than re-implementing it.

Key classes:
- TenantScopedRepository: generic agency-scoped CRUD base for tenant tables.

Key functions:
- (none)

Notes:
- The generic is bound to `AgencyScopedMixin`, so the model is guaranteed to expose the
  NOT NULL `id` + `agency_id` columns the scope filter relies on (compile-time safety).
- `get`/`list` NEVER return rows outside the bound agency; `add` stamps `agency_id` so a
  caller cannot persist a row into the wrong tenant (defense-in-depth, §4.2 re-enforcement).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.base import AgencyScopedMixin

ModelT = TypeVar("ModelT", bound=AgencyScopedMixin)


class TenantScopedRepository(Generic[ModelT]):
    """Generic agency-scoped CRUD base; every query is filtered by `agency_id`."""

    def __init__(self, session: AsyncSession, model: type[ModelT], agency_id: uuid.UUID) -> None:
        """Bind the session, the tenant model type, and the agency scope."""
        self._session = session
        self._model = model
        self._agency_id = agency_id

    @property
    def agency_id(self) -> uuid.UUID:
        """The tenant (agency) scope this repository enforces."""
        return self._agency_id

    async def get(self, entity_id: uuid.UUID) -> ModelT | None:
        """Return the row with this id IFF it belongs to the bound agency, else None."""
        stmt = select(self._model).where(
            self._model.id == entity_id,
            self._model.agency_id == self._agency_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(self, *, limit: int = 50, offset: int = 0) -> Sequence[ModelT]:
        """Return up to `limit` rows for the bound agency (cross-tenant rows excluded)."""
        stmt = (
            select(self._model)
            .where(self._model.agency_id == self._agency_id)
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def add(self, entity: ModelT) -> ModelT:
        """Stamp the row's `agency_id` to the bound scope, persist (flush), and return it."""
        entity.agency_id = self._agency_id
        self._session.add(entity)
        await self._session.flush()
        return entity
