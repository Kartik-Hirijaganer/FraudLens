"""Summary: Close Supabase's implicit public Data API grants and require RLS on every
table in the exposed `public` schema.

Key classes:
- (none)

Key functions:
- upgrade: revoke web-role privileges/defaults, enable RLS, and harden public functions.
- downgrade: intentionally retain the security boundary during schema rollback.

Notes:
- The backend connects directly with its administrative Postgres role; the SPA uses only
  Supabase Auth. Neither path requires `anon`/`authenticated` table privileges.
- PostgreSQL-only statements are skipped by the SQLite migration test suite.
- The downgrade is deliberately non-destructive: automatically reopening the Data API
  during rollback would be an unsafe and surprising side effect.

Revision ID: 0004_harden_supabase_access
Revises: 0003_add_alert_origin
Create Date: 2026-08-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_harden_supabase_access"
down_revision: str | None = "0003_add_alert_origin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POSTGRESQL_DIALECT = "postgresql"

_POSTGRES_HARDENING_STATEMENTS = (
    "revoke all privileges on all tables in schema public from anon, authenticated",
    "revoke all privileges on all sequences in schema public from anon, authenticated",
    """
    alter default privileges in schema public
      revoke all privileges on tables from anon, authenticated
    """,
    """
    alter default privileges in schema public
      revoke all privileges on sequences from anon, authenticated
    """,
    """
    do $fraudlens$
    declare
      app_table record;
    begin
      for app_table in
        select n.nspname as schema_name, c.relname as table_name
          from pg_class c
          join pg_namespace n on n.oid = c.relnamespace
         where n.nspname = 'public'
           and c.relkind in ('r', 'p')
         order by c.relname
      loop
        execute format(
          'alter table %I.%I enable row level security',
          app_table.schema_name,
          app_table.table_name
        );
      end loop;
    end
    $fraudlens$
    """,
    "create schema if not exists fraudlens_security",
    "revoke all privileges on schema fraudlens_security from public, anon, authenticated",
    """
    create or replace function fraudlens_security.enforce_public_object_security()
    returns event_trigger
    language plpgsql
    security definer
    set search_path = pg_catalog
    as $fraudlens$
    declare
      ddl_command record;
    begin
      for ddl_command in select * from pg_event_trigger_ddl_commands()
      loop
        if ddl_command.schema_name = 'public'
           and ddl_command.object_type in ('table', 'partitioned table') then
          execute format(
            'alter table %s enable row level security',
            ddl_command.object_identity
          );
          execute format(
            'revoke all privileges on table %s from anon, authenticated',
            ddl_command.object_identity
          );
        elsif ddl_command.schema_name = 'public'
              and ddl_command.object_type = 'sequence' then
          execute format(
            'revoke all privileges on sequence %s from anon, authenticated',
            ddl_command.object_identity
          );
        elsif ddl_command.schema_name = 'public'
              and ddl_command.object_type in ('view', 'materialized view', 'foreign table') then
          execute format(
            'revoke all privileges on table %s from anon, authenticated',
            ddl_command.object_identity
          );
        elsif ddl_command.schema_name = 'public'
              and ddl_command.object_type in ('function', 'procedure', 'aggregate') then
          execute format(
            'revoke execute on routine %s from public, anon, authenticated',
            ddl_command.object_identity
          );
        end if;
      end loop;
    end
    $fraudlens$
    """,
    """
    revoke execute
      on function fraudlens_security.enforce_public_object_security()
      from public, anon, authenticated
    """,
    "drop event trigger if exists fraudlens_harden_public_objects",
    """
    create event trigger fraudlens_harden_public_objects
      on ddl_command_end
      when tag in (
        'CREATE TABLE',
        'CREATE TABLE AS',
        'SELECT INTO',
        'CREATE SEQUENCE',
        'CREATE VIEW',
        'CREATE MATERIALIZED VIEW',
        'CREATE FOREIGN TABLE',
        'CREATE FUNCTION',
        'CREATE PROCEDURE',
        'CREATE AGGREGATE'
      )
      execute function fraudlens_security.enforce_public_object_security()
    """,
    "revoke execute on all functions in schema public from public, anon, authenticated",
    """
    alter default privileges in schema public
      revoke execute on functions from public, anon, authenticated
    """,
    """
    do $fraudlens$
    begin
      if to_regprocedure('public.custom_access_token_hook(jsonb)') is not null then
        alter function public.custom_access_token_hook(jsonb) set search_path = '';
      end if;
    end
    $fraudlens$
    """,
)


def upgrade() -> None:
    """Apply the PostgreSQL-only Supabase Data API security boundary."""
    if op.get_bind().dialect.name != _POSTGRESQL_DIALECT:
        return
    for statement in _POSTGRES_HARDENING_STATEMENTS:
        op.execute(sa.text(statement))


def downgrade() -> None:
    """Retain RLS and revoked public grants; a rollback must never reopen data access."""
