"""Summary: Attributes month-to-date Azure spend to the resource groups that incurred it and
gates on the part that is supposed to be stable. The subscription-wide budget sees every dollar,
which is its job, but its alert cannot say whether $12.51 is a governed experiment already torn
down or a recurring bill that has quietly doubled. This reads a Cost Management query result,
splits it by the committed `spend_watchdog` policy, and fails on recurring drift or on spend in
any group nobody declared.

Key classes:
- SpendError: safe failure when the cost response cannot be attributed.
- SpendRow: one resource group's month-to-date cost.
- SpendReport: the classified split and the failures it implies.

Key functions:
- parse_query_result: turn a Cost Management response into rows, by column name not position.
- classify: split rows into recurring, ephemeral, system, and unattributed.
- main: read the response, print the attribution table, and exit non-zero on a failure.

Notes:
- Input arrives on stdin (or `--input`) rather than being fetched here, so the gate is testable
  with no Azure credentials and the workflow owns the authenticated read.
- Resource-group names are matched case-insensitively: Cost Management does not promise the same
  casing the portal shows, and a case flip must not silently reclassify a group as unattributed.
- A malformed or empty response is a FAILURE, never an implicit $0. The predecessor to this
  script swallowed a failed read into `|| echo "0"` and reported a passing $0.00 every day.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from lib.azure_cost.config import (
    REPO_ROOT,
    CostModelConfigError,
    SpendWatchdog,
    load_cost_model_config,
)

_COST_COLUMN = "Cost"
_GROUP_COLUMN = "ResourceGroupName"


class SpendError(RuntimeError):
    """Raised when the cost response cannot be read as an attributable result."""


class SpendRow(BaseModel):
    """One resource group's month-to-date cost."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    resource_group: str = Field(..., min_length=1, description="Resource group that was billed.")
    cost_usd: Decimal = Field(..., description="Month-to-date actual cost in USD.")


class SpendReport(BaseModel):
    """Month-to-date spend split by the committed attribution policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    recurring: tuple[SpendRow, ...] = Field(..., description="Always-on groups.")
    ephemeral: tuple[SpendRow, ...] = Field(..., description="Governed per-session groups.")
    system: tuple[SpendRow, ...] = Field(..., description="Free Azure-created groups.")
    unattributed: tuple[SpendRow, ...] = Field(..., description="Groups nobody declared.")

    @property
    def recurring_usd(self) -> Decimal:
        return sum((row.cost_usd for row in self.recurring), Decimal("0"))

    @property
    def ephemeral_usd(self) -> Decimal:
        return sum((row.cost_usd for row in self.ephemeral), Decimal("0"))

    @property
    def unattributed_usd(self) -> Decimal:
        return sum((row.cost_usd for row in self.unattributed), Decimal("0"))

    @property
    def total_usd(self) -> Decimal:
        return (
            self.recurring_usd
            + self.ephemeral_usd
            + self.unattributed_usd
            + sum((row.cost_usd for row in self.system), Decimal("0"))
        )


def parse_query_result(payload: object) -> tuple[SpendRow, ...]:
    """Return one row per resource group, reading columns by NAME rather than position.

    Cost Management is free to reorder or add columns; a positional read would silently attribute
    a currency string as a cost the first time it did.
    """
    if not isinstance(payload, dict):
        raise SpendError("cost response is not a JSON object")
    properties = payload.get("properties")
    if not isinstance(properties, dict):
        raise SpendError("cost response has no properties object")
    columns = properties.get("columns")
    rows = properties.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise SpendError("cost response has no columns/rows arrays")

    names = [column.get("name") if isinstance(column, dict) else None for column in columns]
    for required in (_COST_COLUMN, _GROUP_COLUMN):
        if required not in names:
            raise SpendError(f"cost response is missing the {required} column")
    cost_at = names.index(_COST_COLUMN)
    group_at = names.index(_GROUP_COLUMN)

    parsed: list[SpendRow] = []
    for row in rows:
        if not isinstance(row, list) or len(row) <= max(cost_at, group_at):
            raise SpendError(f"cost row is malformed: {row!r}")
        group = row[group_at]
        if not isinstance(group, str) or not group.strip():
            raise SpendError(f"cost row has no resource group: {row!r}")
        try:
            cost = Decimal(str(row[cost_at]))
        except (InvalidOperation, ValueError) as error:
            raise SpendError(f"cost row has a non-numeric cost: {row!r}") from error
        parsed.append(SpendRow(resource_group=group.strip(), cost_usd=cost))
    return tuple(parsed)


def classify(rows: Sequence[SpendRow], policy: SpendWatchdog) -> SpendReport:
    """Split rows into the committed categories, matching group names case-insensitively."""
    recurring = {name.casefold() for name in policy.recurring_resource_groups}
    ephemeral = {name.casefold() for name in policy.ephemeral_resource_groups}
    system = {name.casefold() for name in policy.system_resource_groups}

    buckets: dict[str, list[SpendRow]] = {
        "recurring": [],
        "ephemeral": [],
        "system": [],
        "unattributed": [],
    }
    for row in rows:
        key = row.resource_group.casefold()
        if key in recurring:
            buckets["recurring"].append(row)
        elif key in ephemeral:
            buckets["ephemeral"].append(row)
        elif key in system:
            buckets["system"].append(row)
        else:
            buckets["unattributed"].append(row)
    return SpendReport(
        recurring=tuple(buckets["recurring"]),
        ephemeral=tuple(buckets["ephemeral"]),
        system=tuple(buckets["system"]),
        unattributed=tuple(buckets["unattributed"]),
    )


def _render(report: SpendReport, threshold: Decimal) -> str:
    """Return the human-readable attribution table."""
    lines = ["month-to-date spend by attribution:", ""]
    for label, group in (
        ("recurring", report.recurring),
        ("ephemeral (ledgered)", report.ephemeral),
        ("system (free)", report.system),
        ("UNATTRIBUTED", report.unattributed),
    ):
        subtotal = sum((row.cost_usd for row in group), Decimal("0"))
        lines.append(f"  {label:<22} ${subtotal:>9.4f}")
        for row in sorted(group, key=lambda item: -item.cost_usd):
            lines.append(f"      {row.resource_group:<34} ${row.cost_usd:>9.4f}")
    lines.extend(
        [
            "",
            f"  {'TOTAL':<22} ${report.total_usd:>9.4f}",
            f"  recurring threshold      ${threshold:>9.4f}",
        ]
    )
    return "\n".join(lines)


def _failures(report: SpendReport, threshold: Decimal) -> list[str]:
    """Return every condition that should fail the watchdog."""
    failures: list[str] = []
    if report.recurring_usd > threshold:
        failures.append(
            f"recurring spend is ${report.recurring_usd:.4f}, above the "
            f"${threshold:.2f} threshold — the always-on bill has drifted; this is NOT "
            f"experiment spend and will not stop on its own"
        )
    for row in report.unattributed:
        failures.append(
            f"unattributed spend ${row.cost_usd:.4f} in resource group {row.resource_group} — "
            f"it is in no committed recurring, ephemeral, or system list; either it is a "
            f"leftover or config/cost-model.yaml needs to declare it"
        )
    return failures


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Attribute month-to-date Azure spend and gate on recurring drift."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Cost Management query response JSON; defaults to stdin.",
    )
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Read the response, print the attribution table, and exit non-zero on a failure."""
    args = _build_parser().parse_args(argv)
    raw = args.input.read_text(encoding="utf-8") if args.input else sys.stdin.read()
    try:
        config = load_cost_model_config(args.repo_root)
        # An unreadable response is a failed check, never an implicit zero.
        payload = json.loads(raw) if raw.strip() else None
        if payload is None:
            raise SpendError("cost response was empty — the authenticated read did not return")
        report = classify(parse_query_result(payload), config.spend_watchdog)
    except (CostModelConfigError, SpendError) as error:
        print(f"::error::azure-spend-check failed to read cost data: {error}")
        return 2
    except json.JSONDecodeError as error:
        print(f"::error::azure-spend-check received invalid JSON: {error}")
        return 2

    threshold = config.spend_watchdog.recurring_monthly_usd
    print(_render(report, threshold))
    failures = _failures(report, threshold)
    if failures:
        print()
        for failure in failures:
            print(f"::error::{failure}")
        return 1
    print("\nazure-spend-check OK: recurring spend is under threshold and every group is declared")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
