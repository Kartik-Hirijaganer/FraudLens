"""Summary: Authentication/authorization dependencies that FAIL CLOSED. A request
must present a verifiable bearer token whose agency_id claim matches the requested
tenant; anything else is rejected (401 for missing/invalid credentials, 403 for a
tenant mismatch). Token verification is pluggable via the TokenVerifier dependency;
the default is intentionally unconfigured (no Akeyless-provided signing key is
wired yet) and therefore rejects every token — so the surface is locked by default
and real verification is added later without touching call sites. The dev bypass is
honored ONLY when settings.is_dev_bypass_enabled, which is False in prod regardless
of the flag, so production can never be bypassed.

Key classes:
- AccessClaims: validated claims extracted from a verified token.
- CredentialsError: raised by a TokenVerifier when a token is not acceptable.
- TokenVerifier: protocol for pluggable token verification.

Key functions:
- get_app_settings: dependency returning the settings bound to the app instance.
- get_token_verifier: dependency returning the (overridable) default verifier.
- authenticate: resolve AccessClaims, honoring the prod-inert dev bypass.
- enforce_tenant: validate a claim's agency_id against the requested agency_id.
- get_tenant_for_path: dependency enforcing tenancy for /agencies/{agency_id}.

Notes:
- enforce_tenant delegates to fraudlens_core.require_agency_id and maps its
  TenantIsolationError to 401 (missing claim) or 403 (mismatch) — no agency id
  value ever appears in the raised message (Aegis tenant/PHI hygiene).
"""

from __future__ import annotations

from typing import Annotated, Protocol, cast

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from starlette.requests import Request

from fraudlens_backend.models.common import TenantContext
from fraudlens_backend.settings import AppSettings
from fraudlens_core import TenantIsolationError, require_agency_id

DEV_BYPASS_AGENCY_ID = "dev-agency"


def get_app_settings(request: Request) -> AppSettings:
    """Return the settings bound to the running app instance (set by the factory)."""
    return cast(AppSettings, request.app.state.settings)


class AccessClaims(BaseModel):
    """Claims extracted from a verified access token (internal, snake_case)."""

    agency_id: str = Field(..., min_length=1, description="Tenant id from the token.")


class CredentialsError(Exception):
    """Raised by a TokenVerifier when a presented token is not acceptable."""


class TokenVerifier(Protocol):
    """Verifies a raw bearer token and returns its claims, or raises CredentialsError."""

    def __call__(self, token: str) -> AccessClaims:
        """Return AccessClaims for a valid token; raise CredentialsError otherwise."""
        ...


class _UnconfiguredTokenVerifier:
    """Fail-closed default: no signing key is wired yet, so reject every token."""

    def __call__(self, token: str) -> AccessClaims:
        """Always raise — real verification (Akeyless key) is added later."""
        raise CredentialsError("token verification is not configured")


def get_token_verifier() -> TokenVerifier:
    """Return the default token verifier (overridden in tests / when wired up)."""
    return _UnconfiguredTokenVerifier()


bearer_scheme = HTTPBearer(auto_error=False)

SettingsDep = Annotated[AppSettings, Depends(get_app_settings)]
VerifierDep = Annotated[TokenVerifier, Depends(get_token_verifier)]
CredentialsDep = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


async def authenticate(
    settings: SettingsDep,
    verifier: VerifierDep,
    credentials: CredentialsDep,
) -> AccessClaims:
    """Resolve AccessClaims, applying the prod-inert dev bypass; else verify the token."""
    if settings.is_dev_bypass_enabled:
        return AccessClaims(agency_id=DEV_BYPASS_AGENCY_ID)
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        return verifier(credentials.credentials)
    except CredentialsError as exc:
        raise HTTPException(status_code=401, detail="invalid token") from exc


AuthenticatedClaims = Annotated[AccessClaims, Depends(authenticate)]


def enforce_tenant(claims: AccessClaims, requested_agency_id: str | None) -> TenantContext:
    """Validate the claim's agency_id against the requested tenant; raise 401/403."""
    try:
        agency_id = require_agency_id(claims.agency_id, requested_agency_id)
    except TenantIsolationError as exc:
        if exc.reason == "mismatch":
            raise HTTPException(status_code=403, detail="tenant mismatch") from exc
        raise HTTPException(status_code=401, detail="missing tenant claim") from exc
    return TenantContext(agency_id=agency_id)


async def get_tenant_for_path(
    agency_id: str,
    claims: AuthenticatedClaims,
) -> TenantContext:
    """Tenant-scoping dependency for /agencies/{agency_id}: claim must match the path."""
    return enforce_tenant(claims, agency_id)
