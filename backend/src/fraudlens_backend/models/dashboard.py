"""Summary: Pydantic response models for the dashboard-metrics API (plan §5.3 endpoint 13, §16
Phase 12). `DashboardMetricsResponse` is a `CamelModel` (camelCase wire, snake_case Python,
`extra="forbid"`) composed of small typed sub-aggregates — alert/transaction/run/SAR counts, LLM
cost totals, and model-health signals — so the analyst dashboard renders from one tenant-scoped,
PHI-free payload (counts, version labels, and money-as-string only; never an account, note, or any
input value). The counts are surfaced as explicit named fields per enum member (not an open dict)
so the API contract is self-documenting and the frontend types are exact.

Key classes:
- AlertMetrics: open/pending-review/in-review/escalated/resolved/dismissed alert counts + total.
- TransactionMetrics: total transactions + a count per risk band (unscored bucketed).
- RunMetrics: investigation-run counts by status (pending/running/completed/failed) + total.
- SarMetrics: latest SAR counts per investigation across every workflow status + total.
- LlmCostMetrics: SAR LLM spend (today + all-time USD) + the drafted-SAR count, for cost dashboards.
- ModelHealthMetrics: active/canary version labels + percent, tenant inference count, latest drift.
- DashboardMetricsResponse: the full tenant-scoped dashboard aggregate.

Key functions:
- (none)

Notes:
- Cost fields are strings (matching `sar_drafts.cost_usd` / the SAR view) so the NUMERIC value is
  carried without binary-float drift (plan §11.5 cost dashboards).
- `byRiskBand` is the one open map (its keys are the canonical RiskBand values + `unscored`), since
  a band set could grow; everything else is a fixed, named contract.
"""

from __future__ import annotations

from pydantic import Field

from fraudlens_backend.models.common import CamelModel


class AlertMetrics(CamelModel):
    """Tenant alert counts by status (plan §9.1 `alerts`)."""

    open: int = Field(..., ge=0, description="Alerts awaiting triage.")
    pending_review: int = Field(..., ge=0, description="Alerts forced into human review.")
    in_review: int = Field(..., ge=0, description="Alerts currently in review.")
    escalated: int = Field(..., ge=0, description="Alerts escalated for heightened review.")
    resolved: int = Field(..., ge=0, description="Alerts resolved (a label was written).")
    dismissed: int = Field(..., ge=0, description="Alerts dismissed as not actionable.")
    total: int = Field(..., ge=0, description="All alerts for the tenant.")


class TransactionMetrics(CamelModel):
    """Tenant transaction volume + risk-band distribution (plan §9.1 `transactions`)."""

    total: int = Field(..., ge=0, description="All ingested transactions for the tenant.")
    by_risk_band: dict[str, int] = Field(
        default_factory=dict,
        description="Count per risk band (low|medium|high|critical) plus `unscored`.",
    )


class RunMetrics(CamelModel):
    """Tenant investigation-run counts by status (plan §9.1 `analysis_runs`)."""

    pending: int = Field(..., ge=0, description="Runs queued but not started.")
    running: int = Field(..., ge=0, description="Runs in progress.")
    completed: int = Field(..., ge=0, description="Runs that finished (with or without a SAR).")
    failed: int = Field(..., ge=0, description="Runs whose deterministic core failed.")
    total: int = Field(..., ge=0, description="All investigation runs for the tenant.")


class SarMetrics(CamelModel):
    """Latest tenant SAR per investigation, grouped by its current workflow status."""

    draft: int = Field(..., ge=0, description="Current working SAR drafts.")
    reviewed: int = Field(..., ge=0, description="Historical reviewed-state current SARs.")
    submitted: int = Field(..., ge=0, description="Frozen SARs awaiting independent review.")
    returned: int = Field(..., ge=0, description="Latest SARs returned for maker changes.")
    withdrawn: int = Field(..., ge=0, description="Latest SARs withdrawn from active preparation.")
    approved: int = Field(..., ge=0, description="Approved SARs.")
    rejected: int = Field(..., ge=0, description="Rejected SARs.")
    failed: int = Field(..., ge=0, description="Runs whose SAR could not be drafted (plan §7.5).")
    total: int = Field(..., ge=0, description="Investigations with a current SAR artifact.")


class LlmCostMetrics(CamelModel):
    """Tenant SAR LLM spend for the cost dashboard (plan §7.6, §11.5)."""

    today_usd: str = Field(
        ..., description="SAR LLM USD spend since UTC midnight (string NUMERIC)."
    )
    total_usd: str = Field(..., description="All-time SAR LLM USD spend (string NUMERIC).")
    draft_count: int = Field(
        ..., ge=0, description="SAR drafts that incurred (or attempted) spend."
    )
    agent_cost_usd: dict[str, str] = Field(
        default_factory=dict,
        description="All-time USD spend split by stable agent role (string NUMERIC values).",
    )


class ModelHealthMetrics(CamelModel):
    """Live model-health signals for the dashboard (plan §10.5; acceptance: model health)."""

    active_version_label: str | None = Field(
        default=None, description="The currently active model version label (None when unset)."
    )
    canary_version_label: str | None = Field(
        default=None, description="The canary candidate label, or None when no rollout is live."
    )
    canary_percent: int = Field(..., ge=0, le=100, description="Percent of traffic on the canary.")
    recent_inference_count: int = Field(
        ..., ge=0, description="Tenant hash-only inference records logged (model activity proxy)."
    )
    latest_drift_severity: str | None = Field(
        default=None, description="Severity of the most recent advisory drift report, if any."
    )


class DashboardMetricsResponse(CamelModel):
    """The tenant-scoped dashboard aggregate (plan §5.3 endpoint 13, §16 Phase 12)."""

    alerts: AlertMetrics = Field(..., description="Alert counts by status.")
    transactions: TransactionMetrics = Field(..., description="Transaction volume + risk bands.")
    runs: RunMetrics = Field(..., description="Investigation-run counts by status.")
    sar: SarMetrics = Field(..., description="SAR-draft counts by review status.")
    llm_cost: LlmCostMetrics = Field(..., description="SAR LLM spend for the cost dashboard.")
    model_health: ModelHealthMetrics = Field(..., description="Live model-health signals.")
