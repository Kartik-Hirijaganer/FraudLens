"""Summary: Pydantic request/response models for the investigation surface (plan §5.4, §16
Phase 8; endpoints 6-7). Every model is a `CamelModel`, so the wire is camelCase while Python
stays snake_case, and `extra="forbid"` rejects unknown fields. `InvestigationStartRequest` is the
`POST /investigations` body — Phase 8 scores via the active registry pointer, so the body carries
only the transaction to investigate (the `modelOverride` from the §5.4 endpoint contract is NOT
exposed until the Phase 10 model selector implements routing — an accepted-but-ignored field would
mis-advertise the surface). `InvestigationStartResponse` is the 202 acknowledgement carrying the
`runId` the run is owned under (ADR-016). `InvestigationSnapshotResponse` is the authoritative
`GET /investigations/{runId}` snapshot the SSE observer reconciles against — the run status +
version provenance plus, once the deterministic core has run, the score/band/SHAP/rule-hits and
(best-effort) the grounded citations + SAR draft status. All fields are PHI-free by construction
(scores, feature names, escaped citations, structured facts).

Key classes:
- InvestigationStartRequest: the POST body (the transaction to investigate).
- InvestigationStartResponse: the 202 acknowledgement (runId).
- InvestigationSnapshotResponse: the authoritative run snapshot (status + results + provenance).

Key functions:
- (none)

Notes:
- `modelOverride` is intentionally omitted in v1: Phase 8 always scores via the active deployment
  pointer, so the field is added with real routing behavior in Phase 10, never as a silent no-op.
- The result/SAR/citation fields are optional because they fill in as the pipeline progresses, so a
  snapshot taken mid-run returns exactly what is durable so far (the SSE stream carries the rest).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from fraudlens_backend.models.common import CamelModel


class InvestigationStartRequest(CamelModel):
    """The `POST /investigations` body: the transaction to investigate (active-model scoring)."""

    transaction_id: uuid.UUID = Field(..., description="Id of the transaction to investigate.")


class InvestigationStartResponse(CamelModel):
    """The 202 acknowledgement: the run id the investigation is owned under (ADR-016)."""

    run_id: str = Field(..., description="The persisted run id; stream/poll it for progress.")


class InvestigationSnapshotResponse(CamelModel):
    """The authoritative investigation snapshot the SSE observer reconciles against."""

    run_id: str = Field(..., description="The run's unique id (UUID).")
    transaction_id: str = Field(..., description="The investigated transaction's id.")
    status: str = Field(..., description="Run status: pending | running | completed | failed.")
    risk_score: float | None = Field(default=None, description="Blended risk score (once scored).")
    risk_band: str | None = Field(default=None, description="Resolved risk band (once scored).")
    fraud_probability: float | None = Field(
        default=None, description="Calibrated model probability (from the result snapshot)."
    )
    model_version: str | None = Field(
        default=None, description="Version label of the model that scored this run."
    )
    rules_version: str | None = Field(
        default=None, description="Fingerprint of the rule set this run evaluated."
    )
    rag_version: str | None = Field(
        default=None, description="Version of the regulatory corpus this run cited."
    )
    prompt_version: str | None = Field(
        default=None, description="Version of the SAR prompt template this run used."
    )
    error_code: str | None = Field(
        default=None, description="Stable code shown to the analyst when a run failed."
    )
    top_features: list[dict[str, Any]] = Field(
        default_factory=list, description="Top SHAP drivers (feature names + numbers, no PHI)."
    )
    rule_hits: list[dict[str, Any]] = Field(
        default_factory=list, description="Fired deterministic rule hits (PHI-free findings)."
    )
    citations: list[dict[str, Any]] = Field(
        default_factory=list, description="Grounded regulatory citations the SAR relied on."
    )
    sar_status: str | None = Field(default=None, description="SAR draft status (draft | failed).")
    sar_draft_id: str | None = Field(default=None, description="Persisted SAR draft id, if any.")
    created_at: datetime = Field(..., description="When the run was created.")
    updated_at: datetime = Field(..., description="When the run was last updated.")
