"""Summary: Investigation-pipeline and analyst-review fields mixed into AppSettings.

Key classes:
- InvestigationRuntimeFields: typed history, retrieval, batch, and review settings.

Key functions:
- (none)

Notes:
- Durable worker lease settings join this responsibility-owned model in Phase 8.
- `llm_daily_budget_usd` is a deployment ceiling, not the tenant's budget: the per-agency value in
  `system_config` is clamped to it, so a misconfigured tenant row can lower live spend but never
  raise it above what this deployment admits.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from fraudlens_backend.settings_defaults import RunExecutionMode


class InvestigationRuntimeFields(BaseModel):
    """Typed investigation and review settings shared by AppSettings."""

    run_execution_mode: RunExecutionMode = Field(
        default="inline",
        description="Run investigations in-process or enqueue them for a durable worker.",
    )
    run_lease_seconds: int = Field(
        default=60,
        gt=0,
        description="Worker lease lifetime before an abandoned run becomes recoverable.",
    )
    run_heartbeat_seconds: int = Field(
        default=10,
        gt=0,
        description="Interval at which a worker extends its active run lease.",
    )
    run_max_attempts: int = Field(
        default=3,
        gt=0,
        description="Maximum fenced worker claims before a run fails permanently.",
    )
    run_deadline_seconds: int = Field(
        default=300,
        gt=0,
        description="Wall-clock deadline applied when a queued investigation is accepted.",
    )
    run_claim_batch: int = Field(
        default=1,
        gt=0,
        description="Maximum runs a worker claims per scheduling pass.",
    )
    run_retry_backoff_seconds: int = Field(
        default=5,
        gt=0,
        description="Base delay before an expired run is eligible for another attempt.",
    )
    run_worker_poll_seconds: float = Field(
        default=1.0,
        gt=0,
        description="Idle delay between durable worker claim attempts.",
    )
    run_worker_heartbeat_file: str = Field(
        default=".local/worker/heartbeat",
        min_length=1,
        description="Worker liveness file updated while its scheduler loop is healthy.",
    )
    run_event_poll_ms: int = Field(
        default=250,
        gt=0,
        description="Worker-mode SSE polling interval for persisted run events.",
    )
    run_event_poll_max_ms: int = Field(
        default=2000,
        gt=0,
        description="Maximum worker-mode SSE polling backoff interval.",
    )
    run_event_heartbeat_seconds: int = Field(
        default=15,
        gt=0,
        description="Maximum quiet interval before worker-mode SSE emits a keepalive comment.",
    )
    investigation_history_window_hours: int = Field(
        default=168,
        gt=0,
        description="Same-account history lookback covering the widest built-in rule window.",
    )
    investigation_history_max: int = Field(
        default=100,
        gt=0,
        description="Maximum same-account history rows loaded per investigation.",
    )
    investigation_rag_top_k: int = Field(
        default=4,
        gt=0,
        description="Number of FinCEN/BSA chunks retrieved for investigation citations.",
    )
    investigation_rag_min_similarity: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Minimum cosine similarity required to surface a vector RAG citation.",
    )
    batch_score_limit: int = Field(
        default=2000,
        gt=0,
        description="Maximum un-investigated transactions processed by one batch-score sweep.",
    )
    review_low_confidence_margin: float = Field(
        default=0.1,
        gt=0,
        le=0.5,
        description="Decision-boundary half-width that forces analyst review.",
    )
    sar_pdf_max_attempts: int = Field(
        default=3,
        gt=0,
        description="Maximum best-effort SAR PDF generation attempts.",
    )
    llm_daily_budget_usd: Decimal = Field(
        default=Decimal("0.25"),
        gt=0,
        description="Deployment ceiling, in USD, on one tenant-day of live LLM spend.",
    )

    @model_validator(mode="after")
    def _valid_run_intervals(self) -> InvestigationRuntimeFields:
        """Keep heartbeats inside leases and polling backoff ordered."""
        if self.run_heartbeat_seconds >= self.run_lease_seconds:
            raise ValueError("run heartbeat interval must be shorter than the lease")
        if self.run_event_poll_max_ms < self.run_event_poll_ms:
            raise ValueError("run event poll maximum must not be below its initial interval")
        return self
