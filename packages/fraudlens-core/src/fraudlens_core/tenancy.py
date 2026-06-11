"""Summary: Multi-tenancy helpers enforcing FraudLens tenant isolation. Every
tenant-scoped operation validates the caller's JWT agency_id claim against the
agency_id of the resource being accessed; a client-supplied tenant id is never
trusted. This module is framework-agnostic (no FastAPI) so it can be reused by
background jobs and the API alike; callers translate TenantIsolationError into
the appropriate transport error (the backend maps it to HTTP 401/403).

Key classes:
- TenantIsolationError: raised when an agency_id claim is missing or mismatched.

Key functions:
- require_agency_id: validate a claim agency_id against the requested agency_id.

Notes:
- Fails closed: a missing claim raises rather than defaulting to "allow".
- No agency_id value is ever placed in an exception message (PHI/tenant hygiene).
- Framework-agnostic: the backend maps TenantIsolationError to HTTP 401/403; the
  API-surface tenant model (camelCase) is fraudlens_backend.models.common.TenantContext.
"""

from __future__ import annotations


class TenantIsolationError(Exception):
    """Raised when a tenant (agency_id) claim is missing or does not match.

    Carries a ``reason`` discriminator (``"missing"`` or ``"mismatch"``) so the
    transport layer can choose 401 vs 403 without parsing a message. The offending
    agency_id values are intentionally NOT included in the message.
    """

    def __init__(self, reason: str) -> None:
        """Store the machine-readable ``reason`` and a static, value-free message."""
        self.reason = reason
        super().__init__(f"tenant isolation check failed: {reason}")


def require_agency_id(claim_agency_id: str | None, requested_agency_id: str | None) -> str:
    """Validate a token's agency_id claim against the requested agency_id.

    Returns the validated agency_id when the claim is present and matches the
    requested resource. Raises :class:`TenantIsolationError` with reason
    ``"missing"`` when the claim is absent/empty, or ``"mismatch"`` when a
    requested agency_id is supplied and differs from the claim. When no specific
    agency is requested, the claim itself is authoritative.
    """
    if not claim_agency_id:
        raise TenantIsolationError("missing")
    if requested_agency_id is not None and requested_agency_id != claim_agency_id:
        raise TenantIsolationError("mismatch")
    return claim_agency_id
