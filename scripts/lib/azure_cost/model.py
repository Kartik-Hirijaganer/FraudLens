"""Summary: The deterministic Azure cost projection. It prices the committed Container Apps
shape at both the idle and the active retail rate, applies the Consumption free grant, adds the
capped log and stored-blob allowance, prices one governed ephemeral AKS session from the
committed node SKUs and counts, and admits that session through the same ADR-028 margin the
experiment ledger uses. Two ceilings are enforced: the AKS session cost and the Container Apps
replica maximum. Every number is a Decimal, so the projection is reproducible to the cent.

Key classes:
- CostLine: one priced line with its arithmetic basis and rate provenance.
- AcaProjection: the recurring Container Apps monthly projection and its ceilings.
- AksProjection: the per-session AKS projection and its admission decision.
- CostModel: the complete projection, resolved rates, and enforced-ceiling failures.

Key functions:
- plain_decimal: render a Decimal without exponent notation or trailing-zero noise.
- resolve_rates: narrow every configured selector to exactly one dated, sourced rate.
- project_aca: price the recurring Container Apps surface for one month.
- project_aks: price and admit one governed ephemeral AKS session.
- build_cost_model: assemble the whole projection and collect ceiling failures.

Notes:
- `failures` is data, not an exception: the CLI writes the document either way so the reader can
  see WHY a ceiling was breached, then exits non-zero.
- The free grant is applied as replica-hours because memory and vCPU exhaust it together at the
  committed 0.5 vCPU / 1 GiB shape; whichever binds first is the one that caps the free hours.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from lib.azure_cost.config import ColdStart, CostModelConfig, ExcludedService
from lib.azure_cost.prices import PriceCatalog, ResolvedRate
from lib.azure_cost.shapes import DeploymentShapes
from lib.experiments.budget import Projection, admit, load_budget_config

_SECONDS_PER_HOUR = Decimal("3600")
_HOURS_PER_MONTH = Decimal("730")
_REQUESTS_PER_METER_UNIT = Decimal("1000000")
_CENTS = Decimal("0.01")
_MICRO = Decimal("0.000001")


class CostLine(BaseModel):
    """One priced line of the projection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item: str = Field(..., min_length=1, description="What is being charged.")
    basis: str = Field(..., min_length=1, description="The arithmetic that produced the amount.")
    amount_usd: Decimal = Field(..., ge=0, description="Charge in USD for this line.")
    rate_keys: tuple[str, ...] = Field(default=(), description="Rate keys the line consumed.")


class AcaProjection(BaseModel):
    """The recurring Container Apps monthly projection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    region: str = Field(..., min_length=1, description="Region the projection is priced in.")
    warm_replica_hours: Decimal = Field(..., ge=0, description="Warm replica-hours per month.")
    free_grant_hours: Decimal = Field(..., ge=0, description="Replica-hours the grant covers.")
    billable_hours: Decimal = Field(..., ge=0, description="Replica-hours billed beyond the grant.")
    lines: tuple[CostLine, ...] = Field(..., min_length=1, description="Priced monthly lines.")
    monthly_usd: Decimal = Field(..., ge=0, description="Expected recurring monthly cost.")
    active_rate_ceiling_usd: Decimal = Field(
        ..., ge=0, description="Monthly cost if every hour billed at the active rate."
    )
    log_ceiling_usd: Decimal = Field(
        ..., ge=0, description="Monthly log cost if ingestion sat at the daily cap all month."
    )


class AksProjection(BaseModel):
    """The per-session ephemeral AKS projection and its admission decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    region: str = Field(..., min_length=1, description="Region the session is priced in.")
    session_hours: Decimal = Field(..., gt=0, description="Wall-clock hours of one session.")
    lines: tuple[CostLine, ...] = Field(..., min_length=1, description="Priced session lines.")
    session_usd: Decimal = Field(..., ge=0, description="Projected cost of one session.")
    admission_margin: Decimal = Field(..., ge=0, description="ADR-028 safety margin applied.")
    cost_with_margin_usd: Decimal = Field(..., ge=0, description="Projected cost plus margin.")
    ceiling_usd: Decimal = Field(..., gt=0, description="Per-session admission ceiling.")
    admitted: bool = Field(..., description="Whether the session fits its ceiling with margin.")


class CostModel(BaseModel):
    """The complete projection with its rates, ceilings, and gaps."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    generated_on: str = Field(..., min_length=1, description="Generation date (UTC, ISO-8601).")
    shapes: DeploymentShapes = Field(..., description="Committed shapes the model priced.")
    rates: tuple[ResolvedRate, ...] = Field(..., min_length=1, description="Resolved unit rates.")
    aca: AcaProjection = Field(..., description="Recurring Container Apps projection.")
    aks: AksProjection = Field(..., description="Per-session ephemeral AKS projection.")
    cold_start: ColdStart = Field(
        ..., description="Measured scale-to-zero cold start, or the recorded absence of one."
    )
    excluded: tuple[ExcludedService, ...] = Field(
        ..., min_length=1, description="Deliberately unpriced services."
    )
    fixed_monthly_usd: Decimal = Field(..., ge=0, description="Recurring monthly total in USD.")
    failures: tuple[str, ...] = Field(default=(), description="Enforced-ceiling breaches.")


def _regions(shapes: DeploymentShapes) -> dict[str, str]:
    return {"aca": shapes.aca_region, "aks": shapes.aks_region}


def _skus(shapes: DeploymentShapes) -> dict[str, str]:
    return {
        "aks_system_vm_size": shapes.aks_system_vm_size,
        "aks_user_vm_size": shapes.aks_user_vm_size,
    }


def resolve_rates(
    config: CostModelConfig, shapes: DeploymentShapes, catalog: PriceCatalog
) -> dict[str, ResolvedRate]:
    """Narrow every configured selector to exactly one dated, sourced rate."""
    regions, skus = _regions(shapes), _skus(shapes)
    endpoint = str(config.retail_prices.endpoint)
    resolved: dict[str, ResolvedRate] = {}
    for key, selector in config.meters.items():
        sku = "" if selector.arm_sku_name_source is None else skus[selector.arm_sku_name_source]
        resolved[key] = catalog.resolve(
            key, selector, regions[selector.region_source], endpoint, sku
        )
    return resolved


def plain_decimal(value: Decimal) -> str:
    """Render a Decimal without exponent notation, so 1.0E+2 reads as 100."""
    return f"{value.normalize():f}"


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENTS)


def project_aca(
    config: CostModelConfig, shapes: DeploymentShapes, rates: dict[str, ResolvedRate]
) -> AcaProjection:
    """Price the recurring Container Apps surface for one month."""
    usage = config.aca_usage
    grant = config.aca_free_grant
    vcpu, memory = shapes.aca_vcpu, shapes.aca_memory_gib
    idle_per_second = rates["aca_vcpu_idle"].price_usd * vcpu + (
        rates["aca_memory_idle"].price_usd * memory
    )
    active_per_second = rates["aca_vcpu_active"].price_usd * vcpu + (
        rates["aca_memory_active"].price_usd * memory
    )
    warm_hours = usage.warm_hours_per_day * usage.days_per_month
    grant_hours = min(grant.vcpu_seconds / vcpu, grant.gib_seconds / memory) / _SECONDS_PER_HOUR
    billable_hours = max(Decimal("0"), warm_hours - grant_hours)
    compute = billable_hours * _SECONDS_PER_HOUR * idle_per_second
    billable_requests = max(Decimal("0"), usage.monthly_requests - grant.requests)
    requests = billable_requests / _REQUESTS_PER_METER_UNIT * rates["aca_requests"].price_usd
    log_rate = rates["log_ingestion"].price_usd
    logs = usage.log_expected_gb_per_month * log_rate
    log_ceiling = shapes.log_daily_quota_gb * usage.days_per_month * log_rate
    blob_rate = rates["blob_hot_lrs"].price_usd
    blob = usage.blob_stored_gb * blob_rate
    tfstate = usage.tfstate_stored_gb * blob_rate
    lines = (
        CostLine(
            item=(
                f"Compute — {plain_decimal(billable_hours)} warm replica-hours beyond the free "
                "grant, idle rate"
            ),
            basis=(
                f"{plain_decimal(warm_hours)} warm h/mo "
                f"({plain_decimal(usage.warm_hours_per_day)} h x "
                f"{usage.days_per_month} days) - {plain_decimal(grant_hours)} free h x "
                f"3600 s x ${plain_decimal(idle_per_second)}/replica-second"
            ),
            amount_usd=_money(compute),
            rate_keys=("aca_vcpu_idle", "aca_memory_idle"),
        ),
        CostLine(
            item="Requests",
            basis=(
                f"{plain_decimal(usage.monthly_requests)} requests/mo - "
                f"{plain_decimal(grant.requests)} free @ "
                f"${plain_decimal(rates['aca_requests'].price_usd)}/1M"
            ),
            amount_usd=_money(requests),
            rate_keys=("aca_requests",),
        ),
        CostLine(
            item="Log Analytics ingestion — expected",
            basis=(
                f"{plain_decimal(usage.log_expected_gb_per_month)} GB/mo x "
                f"${plain_decimal(log_rate)}/GB (capped at "
                f"{plain_decimal(shapes.log_daily_quota_gb)} GB/day => max "
                f"${_money(log_ceiling)}/mo)"
            ),
            amount_usd=_money(logs),
            rate_keys=("log_ingestion",),
        ),
        CostLine(
            item="Blob storage — artifacts and SAR PDFs",
            basis=(
                f"{plain_decimal(usage.blob_stored_gb)} GB hot LRS x "
                f"${plain_decimal(blob_rate)}/GB-month"
            ),
            amount_usd=_money(blob),
            rate_keys=("blob_hot_lrs",),
        ),
        CostLine(
            item="Blob storage — Terraform remote state",
            basis=(
                f"{plain_decimal(usage.tfstate_stored_gb)} GB hot LRS x "
                f"${plain_decimal(blob_rate)}/GB-month"
            ),
            amount_usd=_money(tfstate),
            rate_keys=("blob_hot_lrs",),
        ),
    )
    active_billable = max(Decimal("0"), _HOURS_PER_MONTH * shapes.aca_max_replicas - grant_hours)
    active_ceiling = (
        active_billable * _SECONDS_PER_HOUR * active_per_second + log_ceiling + blob + tfstate
    )
    return AcaProjection(
        region=shapes.aca_region,
        warm_replica_hours=warm_hours,
        free_grant_hours=grant_hours,
        billable_hours=billable_hours,
        lines=lines,
        monthly_usd=_money(sum((line.amount_usd for line in lines), Decimal("0"))),
        active_rate_ceiling_usd=_money(active_ceiling),
        log_ceiling_usd=_money(log_ceiling),
    )


def _aks_lines(
    config: CostModelConfig, shapes: DeploymentShapes, rates: dict[str, ResolvedRate]
) -> tuple[CostLine, ...]:
    session = config.aks_session
    hours = session.hours
    system_rate = rates["aks_system_node"].price_usd
    user_rate = rates["aks_user_node"].price_usd
    lb_rate = rates["standard_load_balancer"].price_usd
    ip_rate = rates["standard_public_ip"].price_usd
    scale_out_nodes = max(0, shapes.aks_user_max_count - shapes.aks_user_min_count)
    return (
        CostLine(
            item="AKS control plane — Free tier",
            basis="sku_tier = Free: the managed control plane is not billed",
            amount_usd=Decimal("0.00"),
        ),
        CostLine(
            item=f"1 x {shapes.aks_system_vm_size} system node",
            basis=f"{plain_decimal(hours)} h x ${plain_decimal(system_rate)}/h",
            amount_usd=_money(hours * system_rate),
            rate_keys=("aks_system_node",),
        ),
        CostLine(
            item=f"{shapes.aks_user_min_count} x {shapes.aks_user_vm_size} user node (baseline)",
            basis=(
                f"{shapes.aks_user_min_count} x {plain_decimal(hours)} h x "
                f"${plain_decimal(user_rate)}/h"
            ),
            amount_usd=_money(shapes.aks_user_min_count * hours * user_rate),
            rate_keys=("aks_user_node",),
        ),
        CostLine(
            item=f"{scale_out_nodes} x {shapes.aks_user_vm_size} user node during HPA scale-out",
            basis=(
                f"{scale_out_nodes} x {plain_decimal(session.scale_out_hours)} h x "
                f"${plain_decimal(user_rate)}/h"
            ),
            amount_usd=_money(scale_out_nodes * session.scale_out_hours * user_rate),
            rate_keys=("aks_user_node",),
        ),
        CostLine(
            item=f"{session.load_balancers} x Standard Load Balancer",
            basis=(
                f"{session.load_balancers} x {plain_decimal(hours)} h x ${plain_decimal(lb_rate)}/h"
            ),
            amount_usd=_money(session.load_balancers * hours * lb_rate),
            rate_keys=("standard_load_balancer",),
        ),
        CostLine(
            item=f"{session.public_ips} x Standard static public IP",
            basis=(
                f"{session.public_ips} x {plain_decimal(hours)} h x ${plain_decimal(ip_rate)}/h"
            ),
            amount_usd=_money(session.public_ips * hours * ip_rate),
            rate_keys=("standard_public_ip",),
        ),
        CostLine(
            item="AKS Log Analytics",
            basis="monitoring_enabled = false: no workspace is created for the session",
            amount_usd=Decimal("0.00"),
        ),
    )


def project_aks(
    config: CostModelConfig,
    shapes: DeploymentShapes,
    rates: dict[str, ResolvedRate],
    admission_margin: Decimal,
) -> AksProjection:
    """Price one governed ephemeral AKS session and admit it against its ceiling."""
    lines = _aks_lines(config, shapes, rates)
    total = sum((line.amount_usd for line in lines), Decimal("0"))
    projection = Projection(
        projected_hours=config.aks_session.hours,
        projected_cost_usd=total,
        measurement_count=1,
    )
    decision = admit(projection, config.ceilings.aks_session_usd, admission_margin=admission_margin)
    return AksProjection(
        region=shapes.aks_region,
        session_hours=config.aks_session.hours,
        lines=lines,
        session_usd=_money(total),
        admission_margin=admission_margin,
        cost_with_margin_usd=_money(decision.cost_with_margin_usd),
        ceiling_usd=decision.allocation_usd,
        admitted=decision.admitted,
    )


def _ceiling_failures(
    config: CostModelConfig, shapes: DeploymentShapes, aks: AksProjection
) -> tuple[str, ...]:
    failures: list[str] = []
    if shapes.aca_max_replicas > config.ceilings.aca_max_replicas:
        failures.append(
            f"Container Apps max_replicas is {shapes.aca_max_replicas}; the committed ceiling is "
            f"{config.ceilings.aca_max_replicas} replica"
        )
    if not aks.admitted:
        failures.append(
            f"AKS session projects ${aks.session_usd} (${aks.cost_with_margin_usd} with the "
            f"{aks.admission_margin} margin), above the ${aks.ceiling_usd} session ceiling"
        )
    return tuple(failures)


def build_cost_model(
    config: CostModelConfig,
    shapes: DeploymentShapes,
    catalog: PriceCatalog,
    generated_on: str,
    repo_root: Path,
) -> CostModel:
    """Assemble the whole projection and collect every enforced-ceiling breach."""
    rates = resolve_rates(config, shapes, catalog)
    margin = load_budget_config(repo_root).admission_margin
    aca = project_aca(config, shapes, rates)
    aks = project_aks(config, shapes, rates, margin)
    return CostModel(
        generated_on=generated_on,
        shapes=shapes,
        rates=tuple(rates[key] for key in sorted(rates)),
        aca=aca,
        aks=aks,
        cold_start=config.cold_start,
        excluded=config.excluded,
        fixed_monthly_usd=aca.monthly_usd,
        failures=_ceiling_failures(config, shapes, aks),
    )
