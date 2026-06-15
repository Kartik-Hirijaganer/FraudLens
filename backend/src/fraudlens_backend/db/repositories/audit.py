"""Summary: The audit helper (plan §9.1 `audit_logs`, §16 Phase 9). `AuditLogRepository` is the
single seam through which the review workflow records its append-only, PHI-free audit trail — one
row per human action (assign/escalate/resolve/dismiss, SAR approve/reject/edit) so the compliance
posture (plan §8.4 "every action audited") holds by construction rather than per call site. It binds
the request's tenant (`agency_id`) and correlation id (`request_id`) at construction and stamps them
onto every row, so a caller cannot forget the tenant scope or the request correlation. `audit_logs`
carries a **nullable** `agency_id` (a global-or-tenant `IdMixin` table), so this repository is
standalone rather than a `TenantScopedRepository`; the metadata it records is scrubbed
(field/reason/status only) and never the raw value, so PHI cannot leak through the audit trail.

Key classes:
- AuditLogRepository: append-only writer for the `audit_logs` table (tenant + request bound).

Key functions:
- (none)

Notes:
- `metadata` holds only PHI-free context (action, resulting status, label, assignee id) — never a
  note/SAR body or any account identifier (mirrors the §8.4 envelope-detail discipline).
- The actor id is passed per call (the verified acting user); a missing actor is the caller's
  concern (the API fails closed before recording an action without an actor).
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import AuditLog


class AuditLogRepository:
    """Append-only writer for `audit_logs`, bound to one tenant + request correlation id."""

    def __init__(self, session: AsyncSession, *, agency_id: uuid.UUID, request_id: str) -> None:
        """Bind the session, the tenant scope, and the request's correlation id."""
        self._session = session
        self._agency_id = agency_id
        self._request_id = request_id

    async def record(
        self,
        *,
        actor_id: uuid.UUID | None,
        action: str,
        resource_type: str,
        resource_id: str | None,
        metadata: dict[str, str] | None = None,
    ) -> AuditLog:
        """Append one PHI-free audit row for an action on a resource (flushed, not committed)."""
        row = AuditLog(
            agency_id=self._agency_id,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            meta=metadata or {},
            request_id=self._request_id,
        )
        self._session.add(row)
        await self._session.flush()
        return row
