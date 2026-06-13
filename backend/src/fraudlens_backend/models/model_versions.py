"""Summary: Pydantic response models for the read-only model-registry API (plan §5.3, §16
Phase 5 — `GET /api/v1/model-versions`). Every model is a `CamelModel`, so the registry is
exposed camelCase on the wire while Python stays snake_case, with `extra="forbid"`. `status`
reuses the canonical `ModelVersionStatus` enum (no duplicated vocabulary, rule 5). The registry
is PLATFORM-global (models are not tenant-scoped, ADR-015), so these carry no `agencyId`; the
list response also surfaces which version is currently ACTIVE so the UI/API consumer can tell
the live model from candidates/archived ones at a glance. `featureSpec`/`metrics` are PHI-free
by construction — feature NAMES + numeric holdout metrics only (plan §9.4).

Key classes:
- ModelVersionResponse: one registry version projected onto the API surface.
- ModelVersionListResponse: the registry versions + the active version label.

Key functions:
- (none)

Notes:
- `metrics` carries only numeric holdout metrics (PR-AUC, recall@budget, ECE, …) and
  `featureSpec` only ordered feature names — never PHI, raw identifiers, or agency ids.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from fraudlens_backend.db.models.enums import ModelVersionStatus
from fraudlens_backend.models.common import CamelModel


class ModelVersionResponse(CamelModel):
    """One model-registry version projected onto the API surface (PHI-free)."""

    version_id: str = Field(..., description="The model version's unique id (UUID).")
    version_label: str = Field(..., description="Unique human-readable version label.")
    status: ModelVersionStatus = Field(..., description="Registry lifecycle status.")
    artifact_uri: str = Field(..., description="Artifact bundle uri (relative to the store).")
    feature_spec: dict[str, Any] = Field(..., description="Ordered feature spec (names only).")
    metrics: dict[str, Any] = Field(..., description="PHI-free holdout metrics recorded.")
    notes: str = Field(..., description="Human-readable notes about the version.")
    created_at: datetime = Field(..., description="When the version was registered.")


class ModelVersionListResponse(CamelModel):
    """The model-registry versions plus the label of the currently active version."""

    versions: list[ModelVersionResponse] = Field(
        default_factory=list, description="Registry versions, newest first."
    )
    active_version_label: str | None = Field(
        default=None, description="Label of the active (deployed) version, if any."
    )
