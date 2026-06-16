"""Summary: Authentication/authorization dependencies that FAIL CLOSED. A request
must present a verifiable bearer token whose agency_id claim matches the requested
tenant; anything else is rejected (401 for missing/invalid credentials, 403 for a
tenant mismatch). Token verification is pluggable via the TokenVerifier dependency;
the default is intentionally unconfigured (no Infisical-provided signing key is
wired yet) and therefore rejects every token — so the surface is locked by default
and real verification is added later without touching call sites. The dev bypass is
honored ONLY when settings.is_dev_bypass_enabled, which is False in prod regardless
of the flag, so production can never be bypassed.

Key classes:
- AccessClaims: validated claims extracted from a verified token.
- CredentialsError: raised by a TokenVerifier when a token is not acceptable.
- TokenVerifier: protocol for pluggable token verification.
- JwksTokenVerifier:

Key functions:
- get_app_settings: dependency returning the settings bound to the app instance.
- get_db_session: dependency yielding an AsyncSession (503 when DB is unconfigured).
- get_token_verifier: dependency returning the (overridable) default verifier.
- authenticate: resolve AccessClaims, honoring the prod-inert dev bypass.
- enforce_tenant: validate a claim's agency_id against the requested agency_id.
- get_tenant_for_path: dependency enforcing tenancy for /agencies/{agencyId}.
- get_tenant: dependency resolving tenant scope from the verified claim alone.
- get_admin_tenant: dependency that additionally requires the admin role (403 otherwise).
- require_actor: return the verified acting user id for an audited mutation (401 when absent).
- optional_actor: return the acting user id when present, else None (audited non-gated mutations).
- audit_writer: build the request-correlated, tenant-scoped audit-log writer (shared by routers).
- rate_limit: build a per-route rate-limit dependency (slowapi-style) — 429 past the budget.

Notes:
- rate_limit (plan §16 Phase 13) is a stricter per-route limiter layered on top of the global
gateway edge limiter (middleware/gateway.py) as defense-in-depth for abuse-prone routes (the
telemetry client-error sink). Its budget/window are read from settings at request time
(config-driven, rule 4) and its per-scope counter lives on app.state.route_rate_limiters, so
state is process-local and test-isolated; v1 runs single-replica (scale-to-zero), so a
per-replica counter is correct (a shared store is the documented multi-replica scale-up).
- enforce_tenant delegates to fraudlens_core.require_agency_id and maps its
TenantIsolationError to 401 (missing claim) or 403 (mismatch) — no agency id
value ever appears in the raised message (FraudLens tenant/PHI hygiene).
- The dev-bypass agency id is the shared demo tenant (fraudlens_backend.demo), so a
bypassed identity resolves to the seeded demo agency in local-demo (still inert in prod);
the bypass mints the CONFIGURED role (settings.auth_dev_bypass_role, default admin so local-demo
can exercise the admin-only model lifecycle, Phase 10) while the audited actor stays DEMO_USER_ID.
- RBAC is claim-based (§6.3): the role rides the verified claim and is re-checked in services
via get_admin_tenant; a non-admin on an admin route fails closed with admin_role_required (403).
- get_db_session yields from app.state.db_sessionmaker; when no DATABASE_URL is configured
the sessionmaker is None and the dependency fails closed with 503 (the app still boots).
- Observability (plan §11.4/§11.7, Phase 12): enforce_tenant binds the verified agency_id/user_id
into the structlog contextvars (access-log correlation) on success, and authenticate +
enforce_tenant emit a PHI-free `auth_fail`/`tenant_mismatch` security event on failure.
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated, Protocol, cast

import jwt
from fastapi import Depends, HTTPException, Path
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError, PyJWKClientError
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from fraudlens_backend.db.models.enums import UserRole
from fraudlens_backend.db.repositories import AuditLogRepository
from fraudlens_backend.demo import DEMO_AGENCY_ID, DEMO_USER_ID
from fraudlens_backend.middleware.logging import bind_identity
from fraudlens_backend.models.common import TenantContext
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.settings import AppSettings
from fraudlens_backend.telemetry import log_security_event
from fraudlens_core import TenantIsolationError, require_agency_id

DEV_BYPASS_AGENCY_ID = str(DEMO_AGENCY_ID)
DEV_BYPASS_USER_ID = str(DEMO_USER_ID)
# The dev-bypass role is config-driven (settings.auth_dev_bypass_role, default admin so local-demo
# can drive the model lifecycle); it is honored only when the bypass is enabled (prod-inert).


def get_app_settings(request: Request) -> AppSettings:
    """Return the settings bound to the running app instance (set by the factory)."""
    return cast(AppSettings, request.app.state.settings)


SettingsDep = Annotated[AppSettings, Depends(get_app_settings)]


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped AsyncSession; fail closed with 503 when DB is unconfigured."""
    sessionmaker = getattr(request.app.state, "db_sessionmaker", None)
    if sessionmaker is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    async with sessionmaker() as session:
        yield session


class AccessClaims(BaseModel):
    """Claims extracted from a verified access token (internal, snake_case)."""

    agency_id: str = Field(..., min_length=1, description="Tenant id from the token.")
    user_id: str | None = Field(
        default=None, description="Acting user id from the token subject (None when absent)."
    )
    role: str = Field(
        default=UserRole.ANALYST.value,
        description="RBAC role claim (analyst|reviewer|admin); least privilege when absent.",
    )


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
        """Always raise — real verification (Infisical key) is added later."""
        raise CredentialsError("token verification is not configured")


class JwksTokenVerifier:
    """Verify RS256 JWTs against the configured Supabase JWKS endpoint."""

    def __init__(self, settings: AppSettings) -> None:
        """Capture the JWKS client and the expected claim/issuer/audience policy."""
        if settings.auth_jwks_url is None:
            raise CredentialsError("jwks url is not configured")
        self._client = PyJWKClient(settings.auth_jwks_url)
        self._algorithm = settings.auth_jwt_algorithm
        self._issuer = settings.auth_jwt_issuer
        self._audience = settings.auth_jwt_audience
        self._agency_claim = settings.auth_agency_claim
        self._role_claim = settings.auth_role_claim

    def __call__(self, token: str) -> AccessClaims:
        """Return verified AccessClaims from a bearer token; raise on any invalid shape."""
        try:
            signing_key = self._client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=[self._algorithm],
                issuer=self._issuer,
                audience=self._audience,
                options={
                    "verify_iss": self._issuer is not None,
                    "verify_aud": self._audience is not None,
                },
            )
        except (InvalidTokenError, PyJWKClientError) as exc:
            raise CredentialsError("token verification failed") from exc
        agency_id = payload.get(self._agency_claim)
        if not isinstance(agency_id, str) or not agency_id:
            raise CredentialsError("missing agency claim")
        role = payload.get(self._role_claim, UserRole.ANALYST.value)
        if role not in {member.value for member in UserRole}:
            raise CredentialsError("invalid role claim")
        subject = payload.get("sub")
        user_id = subject if isinstance(subject, str) and subject else None
        return AccessClaims(agency_id=agency_id, user_id=user_id, role=str(role))


def get_token_verifier(settings: SettingsDep) -> TokenVerifier:
    """Return the configured JWKS verifier, or the fail-closed verifier when absent."""
    if settings.auth_jwks_url is None:
        return _UnconfiguredTokenVerifier()
    return JwksTokenVerifier(settings)


bearer_scheme = HTTPBearer(auto_error=False)

DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]
VerifierDep = Annotated[TokenVerifier, Depends(get_token_verifier)]
CredentialsDep = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


async def authenticate(
    settings: SettingsDep,
    verifier: VerifierDep,
    credentials: CredentialsDep,
) -> AccessClaims:
    """Resolve AccessClaims, applying the prod-inert dev bypass; else verify the token."""
    if settings.is_dev_bypass_enabled:
        return AccessClaims(
            agency_id=DEV_BYPASS_AGENCY_ID,
            user_id=DEV_BYPASS_USER_ID,
            role=settings.auth_dev_bypass_role,
        )
    if credentials is None or not credentials.credentials:
        log_security_event("auth_fail", reason="missing_token")
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        return verifier(credentials.credentials)
    except CredentialsError as exc:
        log_security_event("auth_fail", reason="invalid_token")
        raise HTTPException(status_code=401, detail="invalid token") from exc


AuthenticatedClaims = Annotated[AccessClaims, Depends(authenticate)]


def enforce_tenant(claims: AccessClaims, requested_agency_id: str | None) -> TenantContext:
    """Validate the claim's agency_id against the requested tenant; raise 401/403.

    On success the verified identity is bound into the structlog contextvars so the access log and
    every record in the request carry `agency_id`/`user_id` (plan §11.4); on failure a PHI-free
    security event is emitted (plan §11.7) before failing closed.
    """
    try:
        agency_id = require_agency_id(claims.agency_id, requested_agency_id)
    except TenantIsolationError as exc:
        if exc.reason == "mismatch":
            log_security_event("tenant_mismatch", reason="agency_claim_mismatch")
            raise HTTPException(status_code=403, detail="tenant mismatch") from exc
        log_security_event("auth_fail", reason="missing_tenant_claim")
        raise HTTPException(status_code=401, detail="missing tenant claim") from exc
    bind_identity(agency_id=agency_id, user_id=claims.user_id)
    return TenantContext(agency_id=agency_id, user_id=claims.user_id, role=claims.role)


async def get_tenant_for_path(
    agency_id: Annotated[str, Path(alias="agencyId")],
    claims: AuthenticatedClaims,
) -> TenantContext:
    """Tenant-scoping dependency for /agencies/{agencyId}: claim must match the path."""
    return enforce_tenant(claims, agency_id)


async def get_tenant(claims: AuthenticatedClaims) -> TenantContext:
    """Tenant scope from the verified claim alone (resources never take a path/body tenant)."""
    return enforce_tenant(claims, None)


async def get_admin_tenant(claims: AuthenticatedClaims) -> TenantContext:
    """Tenant scope that additionally requires the admin role (plan §6.3); else 403 fail-closed.

    Backs the admin-only model-lifecycle routes (plan §5.3 endpoints 19-26): authentication +
    tenancy resolve first, then the claim's role is re-checked in the service (the gateway carries
    the same `required_role`, but v1 enforces per-route here, ADR-004).
    """
    tenant = enforce_tenant(claims, None)
    if tenant.role != UserRole.ADMIN.value:
        raise AppError("admin_role_required")
    return tenant


def require_actor(tenant: TenantContext) -> uuid.UUID:
    """Return the verified acting user id, or fail closed (401) when the token carries none.

    Shared by every audited mutation (alert triage, SAR review, model lifecycle) so an
    `actor_id` / `approved_by` is never fabricated from a subject-less token (plan §8.4).
    """
    if not tenant.user_id:
        raise AppError("acting_user_required")
    return uuid.UUID(tenant.user_id)


def optional_actor(tenant: TenantContext) -> uuid.UUID | None:
    """Return the acting user id when the token carries a subject, else None (plan §8.4).

    Used by audited but NOT human-gated mutations (ingest, investigate, rules CRUD): the action
    is still recorded with the tenant + request correlation, but a subject-less token stays valid
    (unlike `require_actor`, which fails closed for the human-review / model-lifecycle gates).
    """
    return uuid.UUID(tenant.user_id) if tenant.user_id else None


def audit_writer(
    tenant: TenantContext, session: AsyncSession, request: Request
) -> AuditLogRepository:
    """Build the request-correlated, tenant-scoped audit writer (plan §8.4; shared by routers)."""
    request_id = str(getattr(request.state, "request_id", "unknown"))
    return AuditLogRepository(session, agency_id=uuid.UUID(tenant.agency_id), request_id=request_id)


class _SlidingWindowLimiter:
    """Per-client sliding-window request counter backing the per-route rate-limit dependency.

    Distinct from the gateway's fixed-window edge limiter: it tracks each client's recent request
    timestamps and rejects once their count within the trailing window exceeds the budget. Keys are
    client hosts; deques decay as their window elapses (an idle key keeps only an empty deque).
    """

    def __init__(self, limit: int, window_seconds: float) -> None:
        """Capture the per-window request budget and the trailing-window length (seconds)."""
        self._limit = limit
        self._window = window_seconds
        self._events: dict[str, deque[float]] = {}

    def over_limit(self, key: str, now: float) -> bool:
        """Record a request at `now` for `key`; True once the trailing window exceeds the budget."""
        events = self._events.get(key)
        if events is None:
            events = deque()
            self._events[key] = events
        cutoff = now - self._window
        while events and events[0] <= cutoff:
            events.popleft()
        events.append(now)
        return len(events) > self._limit


def rate_limit(
    scope: str,
    *,
    limit: Callable[[AppSettings], int],
    window: Callable[[AppSettings], float],
) -> Callable[[Request, AppSettings], Awaitable[None]]:
    """Build a per-route rate-limit dependency (slowapi-style) keyed by client host; 429 on exceed.

    `limit`/`window` resolve from settings at request time so the budget stays config-driven
    (rule 4); the per-scope counter is created lazily and stored on app.state.route_rate_limiters,
    so it is process-local and test-isolated. Layered on the global gateway limiter as
    defense-in-depth for abuse-prone routes (plan §16 Phase 13).
    """

    async def _dependency(request: Request, settings: SettingsDep) -> None:
        """Throttle the calling client for `scope`; raise rate_limited past the budget."""
        limiters: dict[str, _SlidingWindowLimiter] = request.app.state.route_rate_limiters
        limiter = limiters.get(scope)
        if limiter is None:
            limiter = _SlidingWindowLimiter(limit(settings), window(settings))
            limiters[scope] = limiter
        client_host = request.client.host if request.client else "unknown"
        if limiter.over_limit(client_host, time.monotonic()):
            raise AppError("rate_limited")

    return _dependency
