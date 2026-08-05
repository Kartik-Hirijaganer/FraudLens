"""Behavioral checks for the Supabase public-schema hardening migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0004_harden_supabase_access.py"
)


def _load_migration() -> ModuleType:
    """Load the numbered Alembic module without requiring it to be a Python identifier."""
    spec = importlib.util.spec_from_file_location("supabase_hardening_migration", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bind(dialect_name: str) -> SimpleNamespace:
    """Return the minimal Alembic bind shape used by the migration."""
    return SimpleNamespace(dialect=SimpleNamespace(name=dialect_name))


def test_postgres_upgrade_closes_public_data_api(monkeypatch: pytest.MonkeyPatch) -> None:
    migration = _load_migration()
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: _bind("postgresql"))
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: statements.append(str(statement)),
    )

    migration.upgrade()

    sql = "\n".join(statements).lower()
    assert "all tables in schema public from anon, authenticated" in sql
    assert "all sequences in schema public from anon, authenticated" in sql
    assert "alter default privileges in schema public" in sql
    assert "alter table %i.%i enable row level security" in sql
    assert "all functions in schema public from public, anon, authenticated" in sql
    assert "custom_access_token_hook(jsonb) set search_path = ''" in sql
    assert "security definer" in sql
    assert "set search_path = pg_catalog" in sql
    assert "create event trigger fraudlens_harden_public_objects" in sql
    assert "ddl_command.object_type = 'sequence'" in sql
    assert "'view', 'materialized view', 'foreign table'" in sql
    assert "'function', 'procedure', 'aggregate'" in sql
    assert "revoke execute on routine %s from public, anon, authenticated" in sql


def test_non_postgres_upgrade_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    migration = _load_migration()
    statements: list[Any] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: _bind("sqlite"))
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    assert statements == []


def test_downgrade_never_reopens_public_access(monkeypatch: pytest.MonkeyPatch) -> None:
    migration = _load_migration()
    statements: list[Any] = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.downgrade()

    assert statements == []
