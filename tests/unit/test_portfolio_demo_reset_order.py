"""The demo reset must delete every child row before its parent, derived from the ORM metadata.

`reset_story` deletes the demo tenant's operational rows in a hand-maintained order. A table added
later that references one of them is invisible to that list until a reset actually runs against a
database holding such rows — which is how `agent_executions` came to break the live reset with a
`ForeignKeyViolationError` long after it was introduced.

These cases derive the requirement from `Base.metadata` instead of restating the list, so the next
tenant-scoped table to reference a reset target fails here rather than in production.
"""

from __future__ import annotations

from fraudlens_backend.db.models import Base
from fraudlens_backend.portfolio_demo.bootstrap_workflow import _RESET_ORDER

_AGENCY_COLUMN = "agency_id"


def _ordered_tables() -> list[str]:
    return [str(model.__tablename__) for model in _RESET_ORDER]


def test_every_tenant_scoped_child_of_a_reset_target_is_itself_reset() -> None:
    """A child left out of the order blocks its parent's delete with a foreign-key violation."""
    ordered = set(_ordered_tables())
    missing: set[tuple[str, str]] = set()
    for table in Base.metadata.sorted_tables:
        if _AGENCY_COLUMN not in table.c or table.name in ordered:
            continue
        for foreign_key in table.foreign_keys:
            if foreign_key.column.table.name in ordered:
                missing.add((table.name, foreign_key.column.table.name))
    assert not missing, (
        f"tenant-scoped tables referencing a reset target but never reset: {missing}"
    )


def test_each_child_is_deleted_before_the_parent_it_references() -> None:
    """Membership is not enough: the delete order itself has to satisfy the constraints."""
    ordered = _ordered_tables()
    position = {name: index for index, name in enumerate(ordered)}
    by_name = {table.name: table for table in Base.metadata.sorted_tables}
    for name in ordered:
        for foreign_key in by_name[name].foreign_keys:
            parent = foreign_key.column.table.name
            # Self-references are ordered within a single delete, not across two.
            if parent not in position or parent == name:
                continue
            assert position[name] < position[parent], (
                f"{name} is deleted after {parent}, which it references"
            )


def test_the_reset_order_names_each_table_once() -> None:
    """A repeated entry would make the second delete a silent no-op that reads as coverage."""
    ordered = _ordered_tables()
    assert len(ordered) == len(set(ordered))


def test_agent_executions_is_reset_before_the_runs_it_hangs_off() -> None:
    """The exact regression: agent executions reference analysis runs and blocked their delete."""
    ordered = _ordered_tables()
    assert "agent_executions" in ordered
    assert ordered.index("agent_executions") < ordered.index("analysis_runs")
