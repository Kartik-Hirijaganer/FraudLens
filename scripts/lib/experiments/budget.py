"""Summary: Pydantic budget policy, pilot projection, admission, and Markdown
ledger validation for paid FraudLens experiments. Allocations sum to the one-time
ceiling; a full run is admitted only when its projected cost plus configured margin
fits its allocation. Published report run IDs must be represented in the ledger.

Key classes:
- RateQuote: one provenance-bound hourly rate for a provider SKU and purchase option.
- BudgetConfig: validated ceiling, allocations, quotes, margin, and watchdogs.
- PilotMeasurement: elapsed pilot work and the target scale it represents.
- Projection: projected full-run hours and cost derived from pilot measurements.
- Decision: explicit admitted/refused result with the margin-adjusted cost.
- LedgerEntry: one parsed resource-session row from the Markdown ledger.

Key functions:
- load_budget_config: validate the committed experiment budget policy.
- project_cost: scale pilot measurements into a full-run cost projection.
- admit: decide whether a projection plus safety margin fits an allocation.
- load_ledger: parse and validate resource-session rows from the Markdown ledger.
- published_run_ids: discover top-level run IDs in committed benchmark reports.
- check_ledger: enforce plan ceiling, allocations, teardown evidence, and report coverage.

Notes:
- Decimal is used end-to-end so budget decisions never depend on binary-float rounding.
- Historical study rows satisfy provenance coverage but do not consume this plan's $75 ceiling.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

DEFAULT_BUDGET_CONFIG = Path("config/experiments/budget.yaml")
DEFAULT_LEDGER = Path("docs/reference/experiments/ledger.md")
DEFAULT_REPORTS = Path("docs/reference/benchmarks")
DEFAULT_ADMISSION_MARGIN = Decimal("0.30")
REQUIRED_ALLOCATIONS = {
    "azure_cpu_batch",
    "azure_gpu_benchmark",
    "e2e_application_pass",
    "supporting_resources",
    "reserve",
}
LEDGER_HEADERS = (
    "Date",
    "Provider",
    "SKU",
    "Purchase option",
    "Start (UTC)",
    "Stop (UTC)",
    "Hours",
    "Quoted rate USD/hour",
    "Projected cost USD",
    "Actual cost USD",
    "Run ID",
    "Budget scope",
    "Allocation",
    "Teardown verified",
)


class RateQuote(BaseModel):
    """One hourly price with provider provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(..., min_length=1, description="Cloud or compute provider name.")
    sku: str = Field(..., min_length=1, description="Provider-native compute SKU.")
    region: str = Field(..., min_length=1, description="Region in which the quote applies.")
    purchase_option: Literal["spot", "pay_as_you_go"] = Field(
        ..., description="Billing option represented by this quote."
    )
    hourly_rate_usd: Decimal = Field(
        ..., gt=0, decimal_places=6, description="Quoted USD cost for one compute hour."
    )
    price_source_url: HttpUrl = Field(..., description="Authoritative price provenance URL.")
    price_verified_at: date = Field(..., description="Date on which the quote was retrieved.")
    effective_from: date = Field(..., description="Provider-reported rate effective date.")


class BudgetConfig(BaseModel):
    """The one-time experiment ceiling and its enforceable allocations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    currency: Literal["USD"] = Field(..., description="Currency used by every budget value.")
    ceiling_usd: Decimal = Field(..., gt=0, description="Maximum current-plan experiment spend.")
    admission_margin: Decimal = Field(
        ..., ge=0, lt=1, description="Fractional safety margin applied before full-run admission."
    )
    allocations: dict[str, Decimal] = Field(
        ..., min_length=1, description="USD ceilings keyed by experiment allocation."
    )
    watchdog_hours: dict[str, Decimal] = Field(
        ..., min_length=1, description="Maximum session duration per non-reserve allocation."
    )
    rates: dict[str, RateQuote] = Field(
        ..., min_length=1, description="Hourly rate quotes keyed by stable operator names."
    )

    @field_validator("allocations", "watchdog_hours")
    @classmethod
    def _money_and_hours_are_positive(cls, values: dict[str, Decimal]) -> dict[str, Decimal]:
        if any(not key.strip() or value <= 0 for key, value in values.items()):
            raise ValueError("allocation names must be non-blank and values must be positive")
        return values

    @model_validator(mode="after")
    def _allocation_contract_is_exact(self) -> BudgetConfig:
        if set(self.allocations) != REQUIRED_ALLOCATIONS:
            raise ValueError(f"allocations must be exactly {sorted(REQUIRED_ALLOCATIONS)}")
        if sum(self.allocations.values(), Decimal("0")) != self.ceiling_usd:
            raise ValueError("allocations must sum exactly to ceiling_usd")
        expected_watchdogs = REQUIRED_ALLOCATIONS - {"reserve"}
        if set(self.watchdog_hours) != expected_watchdogs:
            raise ValueError(f"watchdog_hours must cover exactly {sorted(expected_watchdogs)}")
        return self


class PilotMeasurement(BaseModel):
    """A pilot's observed duration and the target workload it projects."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rate_key: str = Field(..., min_length=1, description="Rate quote used for this measurement.")
    completed_units: Decimal = Field(..., gt=0, description="Work units completed by the pilot.")
    target_units: Decimal = Field(..., gt=0, description="Work units in the intended full run.")
    elapsed_hours: Decimal = Field(..., ge=0, description="Billable pilot duration in hours.")
    hourly_rate_usd: Decimal = Field(..., gt=0, description="Provenance-bound hourly rate in USD.")


class Projection(BaseModel):
    """A deterministic full-run projection scaled from one or more pilots."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    projected_hours: Decimal = Field(..., ge=0, description="Projected aggregate compute hours.")
    projected_cost_usd: Decimal = Field(..., ge=0, description="Projected aggregate USD cost.")
    measurement_count: int = Field(..., gt=0, description="Pilot measurements used in projection.")


class Decision(BaseModel):
    """The allocation admission result after adding the configured margin."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    admitted: bool = Field(..., description="Whether the full experiment may proceed.")
    allocation_usd: Decimal = Field(..., gt=0, description="Selected allocation ceiling in USD.")
    projected_cost_usd: Decimal = Field(..., ge=0, description="Unmargined projected cost in USD.")
    admission_margin: Decimal = Field(..., ge=0, lt=1, description="Applied safety margin.")
    cost_with_margin_usd: Decimal = Field(
        ..., ge=0, description="Projected cost after the safety margin."
    )
    reason: str = Field(..., min_length=1, description="Stable explanatory admission message.")


class LedgerEntry(BaseModel):
    """One resource session parsed from the experiment Markdown ledger."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_date: date = Field(..., description="Calendar date associated with the session.")
    provider: str = Field(..., min_length=1, description="Compute or API provider.")
    sku: str = Field(..., min_length=1, description="Provider SKU or owned local resource.")
    purchase_option: str = Field(..., min_length=1, description="Spot, PAYG, metered, or owned.")
    started_at: str | None = Field(..., description="UTC start time when recorded.")
    stopped_at: str | None = Field(..., description="UTC stop time when recorded.")
    hours: Decimal = Field(..., ge=0, description="Session duration in hours.")
    quoted_rate_usd: Decimal = Field(..., ge=0, description="Hourly rate recorded for the run.")
    projected_cost_usd: Decimal = Field(..., ge=0, description="Admission-time projected cost.")
    actual_cost_usd: Decimal | None = Field(..., ge=0, description="Settled cost when available.")
    run_id: str = Field(..., min_length=1, description="Unique report or experiment run ID.")
    budget_scope: Literal["current-plan", "historical"] = Field(
        ..., description="Whether this row consumes the current $75 ceiling."
    )
    allocation: str = Field(..., min_length=1, description="Budget allocation or historical.")
    teardown_verified: Literal["yes", "no", "not-applicable"] = Field(
        ..., description="Whether a stopped ephemeral resource was verified absent."
    )


def load_budget_config(repo_root: Path, path: Path = DEFAULT_BUDGET_CONFIG) -> BudgetConfig:
    """Load and validate the experiment budget YAML."""
    resolved = path if path.is_absolute() else repo_root / path
    payload: Any = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    return BudgetConfig.model_validate(payload)


def project_cost(pilot_measurements: Sequence[PilotMeasurement]) -> Projection:
    """Scale completed pilot units linearly into aggregate full-run hours and cost."""
    if not pilot_measurements:
        raise ValueError("at least one pilot measurement is required")
    projected_hours = Decimal("0")
    projected_cost = Decimal("0")
    for measurement in pilot_measurements:
        hours = measurement.elapsed_hours * measurement.target_units / measurement.completed_units
        projected_hours += hours
        projected_cost += hours * measurement.hourly_rate_usd
    return Projection(
        projected_hours=projected_hours,
        projected_cost_usd=projected_cost,
        measurement_count=len(pilot_measurements),
    )


def admit(
    projection: Projection,
    allocation: Decimal,
    *,
    admission_margin: Decimal = DEFAULT_ADMISSION_MARGIN,
) -> Decision:
    """Admit only when projected cost including margin fits the selected allocation."""
    if allocation <= 0:
        raise ValueError("allocation must be positive")
    if admission_margin < 0 or admission_margin >= 1:
        raise ValueError("admission_margin must be in [0, 1)")
    with_margin = projection.projected_cost_usd * (Decimal("1") + admission_margin)
    admitted = with_margin <= allocation
    return Decision(
        admitted=admitted,
        allocation_usd=allocation,
        projected_cost_usd=projection.projected_cost_usd,
        admission_margin=admission_margin,
        cost_with_margin_usd=with_margin,
        reason="projection_within_allocation" if admitted else "projection_exceeds_allocation",
    )


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _optional(value: str) -> str | None:
    return None if value in {"", "—", "-"} else value


def _decimal(value: str) -> Decimal:
    missing = _optional(value)
    if missing is None:
        raise ValueError("required numeric ledger cell is blank")
    return Decimal(missing)


def _optional_decimal(value: str) -> Decimal | None:
    missing = _optional(value)
    return None if missing is None else Decimal(missing)


def load_ledger(path: Path) -> tuple[LedgerEntry, ...]:
    """Parse the resource-session table from the Markdown ledger."""
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if tuple(_cells(line)) != LEDGER_HEADERS:
            continue
        rows: list[LedgerEntry] = []
        for raw in lines[index + 2 :]:
            if not raw.lstrip().startswith("|"):
                break
            cells = _cells(raw)
            if len(cells) != len(LEDGER_HEADERS):
                raise ValueError(
                    f"ledger row has {len(cells)} cells; expected {len(LEDGER_HEADERS)}"
                )
            actual = _optional_decimal(cells[9])
            rows.append(
                LedgerEntry(
                    session_date=date.fromisoformat(cells[0]),
                    provider=cells[1],
                    sku=cells[2],
                    purchase_option=cells[3],
                    started_at=_optional(cells[4]),
                    stopped_at=_optional(cells[5]),
                    hours=_decimal(cells[6]),
                    quoted_rate_usd=_decimal(cells[7]),
                    projected_cost_usd=_decimal(cells[8]),
                    actual_cost_usd=actual,
                    run_id=cells[10],
                    budget_scope=cast(Literal["current-plan", "historical"], cells[11]),
                    allocation=cells[12],
                    teardown_verified=cast(Literal["yes", "no", "not-applicable"], cells[13]),
                )
            )
        return tuple(rows)
    raise ValueError("resource-session table with the required headers was not found")


def published_run_ids(reports_root: Path) -> set[str]:
    """Return top-level run IDs from committed JSON benchmark reports."""
    run_ids: set[str] = set()
    if not reports_root.exists():
        return run_ids
    for path in sorted(reports_root.rglob("*.json")):
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            continue
        run_id = payload.get("runId", payload.get("run_id"))
        if isinstance(run_id, str) and run_id.strip():
            run_ids.add(run_id)
    return run_ids


def check_ledger(
    config: BudgetConfig, entries: Sequence[LedgerEntry], reports_root: Path
) -> list[str]:
    """Return budget, allocation, teardown, uniqueness, and report-coverage failures."""
    errors: list[str] = []
    run_ids = [entry.run_id for entry in entries]
    duplicates = sorted({run_id for run_id in run_ids if run_ids.count(run_id) > 1})
    if duplicates:
        errors.append(f"duplicate ledger run IDs: {duplicates}")
    current = [entry for entry in entries if entry.budget_scope == "current-plan"]
    committed_total = sum(
        (entry.actual_cost_usd if entry.actual_cost_usd is not None else entry.projected_cost_usd)
        for entry in current
    )
    if committed_total > config.ceiling_usd:
        errors.append(
            f"current-plan committed total {committed_total} exceeds {config.ceiling_usd}"
        )
    for entry in current:
        if entry.allocation not in config.allocations:
            errors.append(f"{entry.run_id}: unknown allocation {entry.allocation}")
        if entry.stopped_at is not None and entry.teardown_verified != "yes":
            errors.append(f"{entry.run_id}: stopped resource lacks teardown verification")
    for allocation, ceiling in config.allocations.items():
        allocation_total = sum(
            (
                entry.actual_cost_usd
                if entry.actual_cost_usd is not None
                else entry.projected_cost_usd
            )
            for entry in current
            if entry.allocation == allocation
        )
        if allocation_total > ceiling:
            errors.append(f"allocation {allocation} total {allocation_total} exceeds {ceiling}")
    missing = sorted(published_run_ids(reports_root) - set(run_ids))
    if missing:
        errors.append(f"published report run IDs missing from ledger: {missing}")
    return errors
