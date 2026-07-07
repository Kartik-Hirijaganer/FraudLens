"""Summary: Minimal Supabase Auth Admin API wrapper for Track B user invites. It creates
an auth user through the service-role API, returning the Supabase uid that must be mirrored
into `public.users.id` so JWT `sub` and FraudLens `user_id` reconcile.

Key classes:
- SupabaseAdminError: safe, PHI-free invite failure.
- SupabaseAdminClient: service-role client for admin invites.

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
from dataclasses import dataclass
from typing import Any
from urllib import request

from fraudlens_backend.settings import AppSettings

_INVITE_PATH = "/auth/v1/admin/invite"
_HTTP_CREATED = 201
_HTTP_OK = 200


class SupabaseAdminError(Exception):
    """Raised when Supabase admin provisioning is unavailable or rejects the invite."""


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

    def _post_invite(self, payload: dict[str, str]) -> dict[str, Any]:
        """POST the invite payload with service-role headers and return parsed JSON."""
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.base_url}{_INVITE_PATH}",
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.service_role_key}",
                "apikey": self.service_role_key,
                "Content-Type": "application/json",
            },
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                if response.status not in {_HTTP_OK, _HTTP_CREATED}:
                    raise SupabaseAdminError("supabase invite returned a non-success status")
                raw = response.read().decode("utf-8")
        except OSError as exc:
            raise SupabaseAdminError("supabase invite request failed") from exc
        parsed = json.loads(raw) if raw else {}
        if not isinstance(parsed, dict):
            raise SupabaseAdminError("supabase invite response was not an object")
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
