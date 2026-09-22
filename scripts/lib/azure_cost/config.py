"""Summary: Strict loader for the Azure cost-model policy in config/cost-model.yaml. It
validates the retail-price endpoint, the committed-shape source paths, the two enforced
ceilings, the Container Apps free grant, the warm-window and AKS-session usage assumptions,
the meter selectors resolved against the retail API, and the explicitly excluded services.
No price is stored here — prices resolve live, so a stale committed rate cannot hide.

Key classes:
- RetailPricesSettings: read-only Azure Retail Prices API access parameters.
- ShapeSources: repository-relative Terraform files each committed shape is read from.
- Ceilings: the AKS per-session and Container Apps replica limits the generator enforces.
- FreeGrant: the monthly Container Apps Consumption allowance and its provenance URL.
- AcaUsage: warm-window, request, log, and storage demand priced for Container Apps.
- AksSession: duration and support-resource counts for one governed AKS session.
- ColdStart: the measured scale-to-zero cold start, or the absence of a measurement.
- MeterSelector: the fields that resolve exactly one retail meter, plus a list fallback.
- ExcludedService: one deliberately unpriced service and the reason the gap is acceptable.
- SpendWatchdog: resource-group attribution policy separating recurring spend from governed.
- CostModelConfig: the complete validated cost-model policy.
- CostModelConfigError: safe configuration-load failure.

Key functions:
- load_cost_model_config: load and strictly validate the committed cost-model YAML.

Notes:
- Decimal is used for every money and hour value so projections never depend on binary-float
  rounding, matching lib.experiments.budget.
- `region_source` names which committed shape supplies a selector's region, so changing a
  Terraform `location` moves the priced region with no edit here.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError, model_validator

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_COST_MODEL_CONFIG = Path("config/cost-model.yaml")

RegionSource = Literal["aca", "aks"]
SkuSource = Literal["aks_system_vm_size", "aks_user_vm_size"]


class CostModelConfigError(RuntimeError):
    """Raised when the committed cost-model policy is missing or invalid."""


class RetailPricesSettings(BaseModel):
    """Read-only access parameters for the public Azure Retail Prices API."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint: HttpUrl = Field(..., description="Azure Retail Prices API base endpoint.")
    api_version: str = Field(..., min_length=1, description="Retail Prices API version string.")
    currency: Literal["USD"] = Field(..., description="Billing currency requested from the API.")
    timeout_seconds: int = Field(..., gt=0, description="Per-request HTTP timeout in seconds.")


class ShapeSources(BaseModel):
    """Repository-relative Terraform sources the committed shapes are read from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    aca_tfvars: Path = Field(..., description="Container Apps root tfvars (replicas, region).")
    gateway_module: Path = Field(..., description="Gateway module supplying vCPU and memory.")
    observability_module: Path = Field(..., description="Module supplying the log daily cap.")
    aks_tfvars: Path = Field(..., description="AKS root tfvars (region, node SKUs and counts).")


class Ceilings(BaseModel):
    """The limits that turn this generator into a gate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    aks_session_usd: Decimal = Field(
        ..., gt=0, description="Maximum admissible projected cost for one AKS session."
    )
    aca_max_replicas: int = Field(
        ...,
        gt=0,
        description="Maximum APP-LEVEL Container Apps replicas the committed root may request.",
    )
    aca_concurrent_revisions: int = Field(
        ...,
        gt=0,
        description=(
            "Revisions that may hold replicas at once. Under revision_mode=Multiple a deploy "
            "briefly runs the live and staged revisions together, so the app-level replica bound "
            "is this count times the per-revision max_replicas."
        ),
    )


class FreeGrant(BaseModel):
    """The monthly Container Apps Consumption allowance, applied before any charge."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    vcpu_seconds: Decimal = Field(..., ge=0, description="Free vCPU-seconds per month.")
    gib_seconds: Decimal = Field(..., ge=0, description="Free memory GiB-seconds per month.")
    requests: Decimal = Field(..., ge=0, description="Free requests per month.")
    source_url: HttpUrl = Field(..., description="Published provenance for the free grant.")


class AcaUsage(BaseModel):
    """The Container Apps demand this model prices."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    warm_hours_per_day: Decimal = Field(
        ...,
        ge=0,
        description="Hours a replica is warm each day (24 when min_replicas commits one).",
    )
    monthly_requests: Decimal = Field(..., ge=0, description="Expected requests per month.")
    log_expected_gb_per_month: Decimal = Field(
        ..., ge=0, description="Expected log ingestion per month in GB."
    )
    blob_stored_gb: Decimal = Field(..., ge=0, description="Artifact and SAR PDF blob GB stored.")
    tfstate_stored_gb: Decimal = Field(..., ge=0, description="Terraform remote-state GB stored.")
    days_per_month: int = Field(
        ..., gt=0, description="Days used to convert the log daily cap into a monthly maximum."
    )


class AksSession(BaseModel):
    """Duration and support-resource counts for one governed ephemeral AKS session."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    hours: Decimal = Field(..., gt=0, description="Wall-clock hours of one admitted session.")
    scale_out_hours: Decimal = Field(
        ..., ge=0, description="Hours the HPA demonstration runs a second user node."
    )
    load_balancers: int = Field(..., ge=0, description="Standard Load Balancers billed per hour.")
    public_ips: int = Field(..., ge=0, description="Standard static public IPs billed per hour.")


class ColdStart(BaseModel):
    """The scale-to-zero cold start — a measurement, or the explicit absence of one.

    `min_replicas = 0` is what makes the permanent URL cost about a dollar of compute a month, and
    the keep-warm cron is what hides the cold start it buys. Whether that cron earns its own
    ~$1.25/month depends on a number only the deployed app can produce, so the three measured
    fields stay null until someone takes them and the generated document says so plainly.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: str = Field(..., min_length=1, description="How both measurements are taken.")
    keep_warm_threshold_seconds: Decimal = Field(
        ...,
        gt=0,
        description="Cold start at or below which keep-warm is not worth its monthly cost.",
    )
    measured_at: str | None = Field(
        default=None, description="ISO-8601 date the measurement was taken (null = not yet)."
    )
    cold_seconds: Decimal | None = Field(
        default=None, ge=0, description="Measured seconds for the first request after idle."
    )
    warm_seconds: Decimal | None = Field(
        default=None, ge=0, description="Measured seconds for the request immediately after."
    )

    @model_validator(mode="after")
    def _measurement_is_whole(self) -> ColdStart:
        """Refuse a half-recorded measurement, which would read as evidence it is not."""
        recorded = (self.measured_at, self.cold_seconds, self.warm_seconds)
        if any(value is not None for value in recorded) and not all(
            value is not None for value in recorded
        ):
            raise ValueError(
                "cold_start needs measured_at, cold_seconds, and warm_seconds together"
            )
        return self

    @property
    def measured(self) -> bool:
        """True once a real measurement replaced the empty placeholder."""
        return self.measured_at is not None


class MeterSelector(BaseModel):
    """The fields that must resolve to exactly one retail meter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(..., min_length=1, description="Human-readable meter name in the report.")
    service_name: str = Field(..., min_length=1, description="Retail API `serviceName` value.")
    meter_name: str | None = Field(default=None, description="Retail API `meterName` value.")
    product_name: str | None = Field(default=None, description="Retail API `productName` value.")
    arm_sku_name_source: SkuSource | None = Field(
        default=None, description="Committed shape supplying the `armSkuName` filter."
    )
    unit_of_measure: str = Field(..., min_length=1, description="Asserted retail unit of measure.")
    region_source: RegionSource = Field(..., description="Committed shape supplying the region.")
    tier_minimum_units: Decimal | None = Field(
        default=None, description="Graduated-tier lower bound selecting one price row."
    )
    min_retail_price: Decimal | None = Field(
        default=None, description="Lower bound discarding zero-rated allowance rows."
    )
    exclude_product_substrings: tuple[str, ...] = Field(
        default=(), description="Discard rows whose product name contains any of these."
    )
    exclude_meter_substrings: tuple[str, ...] = Field(
        default=(), description="Discard rows whose meter name contains any of these."
    )
    list_price_usd: Decimal | None = Field(
        default=None, description="Published list rate used when the API returns no meter."
    )
    list_price_source_url: HttpUrl | None = Field(
        default=None, description="Provenance for the published list rate."
    )


class ExcludedService(BaseModel):
    """One service this model deliberately does not price."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    service: str = Field(..., min_length=1, description="Service left out of the projection.")
    reason: str = Field(..., min_length=1, description="Why leaving it out is defensible.")


class SpendWatchdog(BaseModel):
    """Attribution policy that lets the daily watchdog separate drift from governed spend."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    recurring_resource_groups: tuple[str, ...] = Field(
        ..., min_length=1, description="Always-on groups the recurring projection prices."
    )
    ephemeral_resource_groups: tuple[str, ...] = Field(
        ..., description="Per-session experiment groups governed by the ADR-028 ledger."
    )
    system_resource_groups: tuple[str, ...] = Field(
        ..., description="Free Azure-created groups that carry no charge."
    )
    recurring_monthly_usd: Decimal = Field(
        ..., gt=0, description="Recurring month-to-date spend above which drift is declared."
    )


class CostModelConfig(BaseModel):
    """The complete validated Azure cost-model policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    retail_prices: RetailPricesSettings = Field(..., description="Retail Prices API access.")
    shapes: ShapeSources = Field(..., description="Terraform sources for the committed shapes.")
    ceilings: Ceilings = Field(..., description="Enforced session and replica ceilings.")
    spend_watchdog: SpendWatchdog = Field(
        ..., description="Resource-group attribution policy for the daily spend watchdog."
    )
    aca_free_grant: FreeGrant = Field(..., description="Container Apps monthly free allowance.")
    aca_usage: AcaUsage = Field(..., description="Container Apps demand assumptions.")
    aks_session: AksSession = Field(..., description="One governed AKS session shape.")
    cold_start: ColdStart = Field(..., description="Measured scale-to-zero cold start, if taken.")
    meters: dict[str, MeterSelector] = Field(
        ..., min_length=1, description="Meter selectors keyed by stable model name."
    )
    excluded: tuple[ExcludedService, ...] = Field(
        ..., min_length=1, description="Deliberately unpriced services with reasons."
    )


def load_cost_model_config(
    repo_root: Path = REPO_ROOT, path: Path = DEFAULT_COST_MODEL_CONFIG
) -> CostModelConfig:
    """Load and strictly validate the committed cost-model YAML."""
    resolved = path if path.is_absolute() else repo_root / path
    try:
        payload: Any = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except OSError as error:
        raise CostModelConfigError(f"cost-model config is unreadable: {resolved}") from error
    try:
        return CostModelConfig.model_validate(payload)
    except ValidationError as error:
        raise CostModelConfigError(f"cost-model config is invalid: {resolved}\n{error}") from error
