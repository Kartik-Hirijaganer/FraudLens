"""Unit checks for the tracked Supabase custom-claims SQL."""

from __future__ import annotations

from pathlib import Path

_SQL_PATH = Path(__file__).resolve().parents[2] / "supabase" / "2026-07-06-auth-claims.sql"


def test_supabase_auth_sql_stamps_required_claims_and_enables_rls() -> None:
    sql = _SQL_PATH.read_text(encoding="utf-8")

    assert "public.custom_access_token_hook" in sql
    assert "'{agency_id}'" in sql
    assert "'{user_role}'" in sql
    assert "alter table public.users enable row level security" in sql
    assert "users_read_same_agency" in sql
    assert "supabase_auth_admin" in sql
