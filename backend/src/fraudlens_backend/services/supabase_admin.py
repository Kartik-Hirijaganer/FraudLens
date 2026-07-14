"""Summary: Minimal Supabase Auth Admin API wrapper for user provisioning. It creates invites
and idempotently ensures confirmed email/password users through the service-role API, returning
the Supabase uid that must be mirrored into `public.users.id` so JWT `sub` reconciles.

Key classes:
- SupabaseAdminError: safe, PHI-free invite failure.
- SupabaseAuthAppMetadata: server-owned tenant and RBAC claims for verified JWT fallback.
- SupabaseAuthUser: validated subset of a Supabase admin user response.
- SupabaseAuthUsersPage: validated Supabase admin user-list response.
- SupabaseAdminClient: service-role client for admin invites and demo provisioning.

Key functions:
- (none)

Notes:
- The service-role key is read only from settings/env (Infisical `/backend`) and never logged.
- Network IO runs in a worker thread to keep FastAPI handlers async without adding another
  runtime dependency.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib import parse, request

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fraudlens_backend.settings import AppSettings

_INVITE_PATH = "/auth/v1/admin/invite"
_USERS_PATH = "/auth/v1/admin/users"
_HTTP_CREATED = 201
_HTTP_OK = 200
_USER_PAGE_SIZE = 1000


class SupabaseAdminError(Exception):
    """Raised when Supabase admin provisioning is unavailable or rejects the invite."""


class SupabaseAuthAppMetadata(BaseModel):
    """Server-owned authorization claims persisted in Supabase Auth app metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    agency_id: str = Field(..., min_length=1, description="FraudLens tenant UUID claim.")
    user_role: str = Field(..., min_length=1, description="FraudLens canonical RBAC role claim.")


class SupabaseAuthUser(BaseModel):
    """Validated identity fields returned by the Supabase Auth admin API."""

    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID = Field(..., description="Supabase Auth user UUID used as JWT subject.")
    email: str = Field(..., description="Confirmed synthetic or invited user email.")


class SupabaseAuthUsersPage(BaseModel):
    """Validated page returned by the Supabase Auth admin list-users endpoint."""

    model_config = ConfigDict(extra="ignore")

    users: list[SupabaseAuthUser] = Field(..., description="Users returned on this page.")


@dataclass(frozen=True)
class SupabaseAdminClient:
    """Service-role client for the Supabase Auth admin invite endpoint."""

    base_url: str
    service_role_key: str
    timeout_seconds: float = 10.0

    @classmethod
    def from_settings(cls, settings: AppSettings) -> SupabaseAdminClient:
        """Build a client from AppSettings, deriving the project URL from issuer when needed."""
        base_url = settings.supabase_url
        if base_url is None and settings.auth_jwt_issuer:
            base_url = settings.auth_jwt_issuer.removesuffix("/auth/v1")
        if not base_url or not settings.supabase_service_role_key:
            raise SupabaseAdminError("supabase admin API is not configured")
        return cls(
            base_url=base_url.rstrip("/"), service_role_key=settings.supabase_service_role_key
        )

    async def invite_user(self, *, email: str) -> uuid.UUID:
        """Invite a user by email and return the created Supabase auth uid."""
        payload = {"email": email}
        body = await asyncio.to_thread(self._post_invite, payload)
        user_id = _extract_user_id(body)
        if user_id is None:
            raise SupabaseAdminError("supabase invite response omitted user id")
        return user_id

    async def ensure_password_user(
        self,
        *,
        email: str,
        password: str,
        app_metadata: SupabaseAuthAppMetadata,
    ) -> uuid.UUID:
        """Create or refresh one confirmed password user and return its stable auth UUID."""
        return await asyncio.to_thread(self._ensure_password_user, email, password, app_metadata)

    def _ensure_password_user(
        self,
        email: str,
        password: str,
        app_metadata: SupabaseAuthAppMetadata,
    ) -> uuid.UUID:
        """Synchronously ensure one user without exposing credentials in errors or logs."""
        user_id = self._find_user_id(email)
        payload: dict[str, object] = {
            "email": email,
            "password": password,
            "email_confirm": True,
            "app_metadata": app_metadata.model_dump(),
        }
        if user_id is None:
            body = self._request_json("POST", _USERS_PATH, payload)
            user_id = _extract_user_id(body)
            if user_id is None:
                raise SupabaseAdminError("supabase create-user response omitted user id")
            return user_id
        self._request_json("PUT", f"{_USERS_PATH}/{user_id}", payload)
        return user_id

    def _find_user_id(self, email: str) -> uuid.UUID | None:
        """Find an exact user email through bounded Supabase admin pagination."""
        page = 1
        while True:
            query = parse.urlencode({"page": page, "per_page": _USER_PAGE_SIZE})
            body = self._request_json("GET", f"{_USERS_PATH}?{query}")
            try:
                users_page = SupabaseAuthUsersPage.model_validate(body)
            except ValidationError as exc:
                raise SupabaseAdminError("supabase list-users response was invalid") from exc
            for user in users_page.users:
                if user.email.casefold() == email.casefold():
                    return user.id
            if len(users_page.users) < _USER_PAGE_SIZE:
                return None
            page += 1

    def _post_invite(self, payload: dict[str, str]) -> dict[str, Any]:
        """POST the invite payload with service-role headers and return parsed JSON."""
        return self._request_json("POST", _INVITE_PATH, payload)

    def _request_json(
        self,
        method: str,
        path: str,
        payload: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        """Call a Supabase admin endpoint and return a safely validated JSON object."""
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.service_role_key}",
                "apikey": self.service_role_key,
                "Content-Type": "application/json",
            },
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                if response.status not in {_HTTP_OK, _HTTP_CREATED}:
                    raise SupabaseAdminError("supabase admin returned a non-success status")
                raw = response.read().decode("utf-8")
        except OSError as exc:
            raise SupabaseAdminError("supabase admin request failed") from exc
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise SupabaseAdminError("supabase admin response was invalid") from exc
        if not isinstance(parsed, dict):
            raise SupabaseAdminError("supabase admin response was not an object")
        return parsed


def _extract_user_id(body: dict[str, Any]) -> uuid.UUID | None:
    """Extract a UUID from known Supabase admin response shapes."""
    candidates = [body.get("id")]
    user = body.get("user")
    if isinstance(user, dict):
        candidates.append(user.get("id"))
    for candidate in candidates:
        if isinstance(candidate, str):
            try:
                return uuid.UUID(candidate)
            except ValueError:
                continue
    return None
