"""Summary: The multi-tenancy invariant guard (plan §9.3). It inspects the SQLAlchemy
metadata (no database needed) and asserts that every tenant-scoped table carries an
`agency_id` column that is (a) a foreign key to `agencies` and (b) indexed — leading a
composite index, a unique constraint, or carrying its own single-column index. The seven
platform tables (the registry/training tables + `agencies` itself) are explicitly
allowlisted and must NOT carry `agency_id`. The allowlist is sourced from the model package
(`PLATFORM_TABLES`) so the check and the schema cannot drift. Run via `make tenancy-check`;
it is part of the CI gate.

Key classes:
- (none)

Key functions:
- find_violations: return the list of tenancy-invariant violations for a metadata + allowlist.
- main: scan the live FraudLens metadata and return a process exit code.

Notes:
- "Indexed" means `agency_id` is the leading column of some Index/UniqueConstraint, or the
  column itself is indexed — composite indexes leading with `agency_id` count (no redundant
  single-column index is required).
- A platform table that unexpectedly carries `agency_id`, or an allowlisted table missing
  from the schema, is a violation (catches misclassification + stale allowlist).
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import ForeignKey, MetaData, Table, UniqueConstraint

from fraudlens_backend.db.models import PLATFORM_TABLES, Base

_AGENCY_ID = "agency_id"
_AGENCIES_TABLE = "agencies"


def _agency_id_indexed(table: Table) -> bool:
    """Return True when `agency_id` leads an index/unique constraint (or is itself indexed)."""
    column = table.columns.get(_AGENCY_ID)
    if column is None:
        return False
    if column.index:
        return True
    for index in table.indexes:
        cols = list(index.columns)
        if cols and cols[0].name == _AGENCY_ID:
            return True
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint):
            cols = list(constraint.columns)
            if cols and cols[0].name == _AGENCY_ID:
                return True
    return False


def _references_agencies(table: Table) -> bool:
    """Return True when `agency_id` is a foreign key to the `agencies` table."""
    column = table.columns.get(_AGENCY_ID)
    if column is None:
        return False
    return any(
        isinstance(fk, ForeignKey) and fk.column.table.name == _AGENCIES_TABLE
        for fk in column.foreign_keys
    )


def find_violations(metadata: MetaData, platform_tables: Iterable[str]) -> list[str]:
    """Return tenancy-invariant violations for the given metadata + platform allowlist."""
    allowlist = set(platform_tables)
    table_names = set(metadata.tables)
    violations: list[str] = []

    for stale in sorted(allowlist - table_names):
        violations.append(f"{stale}: in platform allowlist but not defined in the schema")

    for name in sorted(table_names):
        table = metadata.tables[name]
        has_agency_id = _AGENCY_ID in table.columns
        if name in allowlist:
            if has_agency_id:
                violations.append(f"{name}: platform table must NOT carry agency_id")
            continue
        if not has_agency_id:
            violations.append(f"{name}: tenant-scoped table is missing agency_id")
            continue
        if not _references_agencies(table):
            violations.append(f"{name}: agency_id must be a foreign key to agencies")
        if not _agency_id_indexed(table):
            violations.append(f"{name}: agency_id must be indexed (lead an index/unique)")
    return violations


def main() -> int:
    """Scan the live FraudLens metadata; print violations and return an exit code."""
    violations = find_violations(Base.metadata, PLATFORM_TABLES)
    for violation in violations:
        print(violation)
    if violations:
        print(f"\ncheck_tenancy FAILED: {len(violations)} tenancy-invariant violation(s)")
        return 1
    tenant_count = len(set(Base.metadata.tables) - set(PLATFORM_TABLES))
    print(
        "check_tenancy OK: "
        f"{tenant_count} tenant tables carry indexed agency_id; "
        f"{len(PLATFORM_TABLES)} platform tables allowlisted"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
