"""Unit tests for the server-only Supabase Auth admin client."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from fraudlens_backend.services.supabase_admin import (
    SupabaseAdminClient,
    SupabaseAdminError,
    SupabaseAuthAppMetadata,
    _extract_user_id,
)

_AUTH_USER_ID = uuid.UUID("77777777-7777-4777-8777-777777777777")


async def test_ensure_password_user_creates_a_missing_confirmed_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def request_json(
        _client: SupabaseAdminClient,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        calls.append((method, path, payload))
        if method == "GET":
            return {"users": []}
        return {"id": str(_AUTH_USER_ID)}

    monkeypatch.setattr(SupabaseAdminClient, "_request_json", request_json)
    client = SupabaseAdminClient("https://project.supabase.test", "service-role-placeholder")

    user_id = await client.ensure_password_user(
        email="analyst@supabase-admin.test",
        password="synthetic-password",
        app_metadata=SupabaseAuthAppMetadata(agency_id="agency-1", user_role="analyst"),
    )

    assert user_id == _AUTH_USER_ID
    assert calls[0][:2] == ("GET", "/auth/v1/admin/users?page=1&per_page=1000")
    assert calls[1] == (
        "POST",
        "/auth/v1/admin/users",
        {
            "email": "analyst@supabase-admin.test",
            "password": "synthetic-password",
            "email_confirm": True,
            "app_metadata": {"agency_id": "agency-1", "user_role": "analyst"},
        },
    )


async def test_ensure_password_user_refreshes_an_existing_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def request_json(
        _client: SupabaseAdminClient,
        method: str,
        path: str,
        _payload: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        calls.append((method, path))
        if method == "GET":
            return {"users": [{"id": str(_AUTH_USER_ID), "email": "ADMIN@SUPABASE-ADMIN.TEST"}]}
        return {"id": str(_AUTH_USER_ID)}

    monkeypatch.setattr(SupabaseAdminClient, "_request_json", request_json)
    client = SupabaseAdminClient("https://project.supabase.test", "service-role-placeholder")

    user_id = await client.ensure_password_user(
        email="admin@supabase-admin.test",
        password="synthetic-password",
        app_metadata=SupabaseAuthAppMetadata(agency_id="agency-1", user_role="admin"),
    )

    assert user_id == _AUTH_USER_ID
    assert calls == [
        ("GET", "/auth/v1/admin/users?page=1&per_page=1000"),
        ("PUT", f"/auth/v1/admin/users/{_AUTH_USER_ID}"),
    ]


def test_invalid_list_response_raises_a_safe_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        SupabaseAdminClient,
        "_request_json",
        lambda *_args, **_kwargs: {"users": [{"email": "missing-id@example.test"}]},
    )
    client = SupabaseAdminClient("https://project.supabase.test", "service-role-placeholder")

    with pytest.raises(SupabaseAdminError, match="list-users response was invalid"):
        client._find_user_id("missing-id@example.test")


def test_extract_user_id_accepts_known_shapes_and_rejects_invalid_values() -> None:
    assert _extract_user_id({"id": str(_AUTH_USER_ID)}) == _AUTH_USER_ID
    assert _extract_user_id({"user": {"id": str(_AUTH_USER_ID)}}) == _AUTH_USER_ID
    assert _extract_user_id({"id": "not-a-uuid"}) is None
