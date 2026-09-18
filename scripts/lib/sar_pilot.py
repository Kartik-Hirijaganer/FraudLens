"""Summary: The pre-registered Tier-3 external-model pilot (release 0.5.0 Phase 3). The cascade's
third stage is a hosted model, and choosing it after seeing 1,000-case results would be choosing
the winner rather than measuring it. So the decision is pre-registered instead: this module loads
the FROZEN case set, candidates, and tie-break from `config/experiments/sar-tier3-pilot.yaml`, and
applies one fixed rule — the cheapest candidate clearing EVERY committed `sar_quality` threshold
wins, ties go to the declared tie-break model — to results measured over the identical set, prompt,
policy, and sampling. Nothing here can soften a threshold: they are read from `config/quality.yaml`.
The cases are real escalation traffic (drafts the deterministic gate actually rejected on the
self-hosted tiers), so the pilot measures the candidates on the only traffic Tier 3 ever sees.
Running a candidate spends money at a hosted provider and is an owner-approved action under Golden
Rule 7; this module contains no provider call and decides nothing until results are handed to it.

Key classes:
- SarPilotCandidate: one declared candidate model reference.
- SarPilotComposition: the declared make-up of the frozen set, cross-checked on load.
- SarPilotManifest: the frozen case set, candidates, provenance, and tie-break.
- SarPilotMeasurement: one candidate's measured outcome over the frozen set.
- SarPilotSelection: the decided Tier-3 model with the reason it won.
- SarPilotError: raised when the manifest is missing, malformed, or internally inconsistent.

Key functions:
- load_pilot_manifest: load and strictly validate the committed pilot manifest.
- select_tier3_model: apply the pre-registered selection rule to measured candidates.

Notes:
- `select_tier3_model` returns a selection with `model=None` when no candidate clears every
  threshold. That is the correct outcome, not an error: the plan's fallback is an honest report
  that the cascade ships with two tiers, never a quietly relaxed bar.
- Cost is compared per 1,000 cases and carried as `Decimal`, because the whole point of the
  tie-break is a price comparison and float rounding would decide it.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lib.quality.config import SarQualityThresholds

DEFAULT_PILOT_MANIFEST = (
    Path(__file__).resolve().parents[2] / "config" / "experiments" / "sar-tier3-pilot.yaml"
)


class SarPilotError(RuntimeError):
    """Raised when the frozen pilot manifest cannot be loaded or validated."""


class SarPilotCandidate(BaseModel):
    """One candidate model the pilot may select as the cascade's external tier."""

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    model: str = Field(..., min_length=1, description="Catalog model reference for the candidate.")


class SarPilotComposition(BaseModel):
    """How the frozen set was composed, recorded rather than implied."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    both_tier_failures: int = Field(
        ..., ge=0, description="Cases the gate rejected on BOTH self-hosted tiers."
    )
    awq_tier_failures: int = Field(
        ..., ge=0, description="Cases the gate rejected on the AWQ tier only."
    )
    total: int = Field(..., gt=0, description="Declared total, cross-checked against the ids.")


class SarPilotManifest(BaseModel):
    """The pre-registered pilot: frozen cases, candidates, provenance, and tie-break."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_version: str = Field(..., min_length=1, description="Auditable pilot policy version.")
    source_run_id: str = Field(..., min_length=1, description="Benchmark run the cases came from.")
    source_cases_sha256: str = Field(
        ..., min_length=1, description="Digest of the case file the run was measured over."
    )
    gate_policy_version: str = Field(
        ..., min_length=1, description="Gate policy the escalation outcomes were derived under."
    )
    composition: SarPilotComposition = Field(..., description="Declared make-up of the frozen set.")
    candidates: tuple[SarPilotCandidate, ...] = Field(
        ..., min_length=2, description="Candidates that run the identical set."
    )
    tie_break_model: str = Field(
        ..., min_length=1, description="Candidate that wins an exact cost tie."
    )
    cases: tuple[str, ...] = Field(..., min_length=1, description="Frozen, ordered case ids.")

    def model_post_init(self, _context: object) -> None:
        """Reject a manifest whose declared composition does not match its own case list."""
        declared = self.composition
        if declared.both_tier_failures + declared.awq_tier_failures != declared.total:
            raise ValueError("pilot composition does not sum to its declared total")
        if len(self.cases) != declared.total:
            raise ValueError("pilot case count does not match its declared total")
        if len(set(self.cases)) != len(self.cases):
            raise ValueError("pilot case ids must be unique")
        if self.tie_break_model not in {candidate.model for candidate in self.candidates}:
            raise ValueError("pilot tie-break model is not one of the candidates")


class SarPilotMeasurement(BaseModel):
    """One candidate's measured outcome over the frozen set, and what it cost."""

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    model: str = Field(..., min_length=1, description="Candidate this result was measured for.")
    cases: int = Field(..., gt=0, description="Cases the candidate was actually run over.")
    gate_pass_rate: float = Field(
        ..., ge=0, le=1, description="Share of drafts the deterministic gate accepted."
    )
    citation_precision: float = Field(
        ..., ge=0, le=1, description="Minimum offered-citation precision over accepted drafts."
    )
    citation_recall: float = Field(
        ..., ge=0, le=1, description="Mean expected-citation recall over accepted drafts."
    )
    required_fact_coverage: float = Field(
        ..., ge=0, le=1, description="Mean required-fact coverage over accepted drafts."
    )
    cost_usd_per_1k_cases: Decimal = Field(
        ..., ge=0, description="Measured spend projected to one thousand cases."
    )


class SarPilotSelection(BaseModel):
    """The decided Tier-3 model, or an explicit non-selection, with its reason."""

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    model: str | None = Field(
        default=None, description="Selected model reference, or None when none qualified."
    )
    reason: str = Field(..., min_length=1, description="Why this candidate won, or why none did.")
    qualified: tuple[str, ...] = Field(
        default=(), description="Candidates that cleared every threshold, cheapest first."
    )
    rejected: tuple[str, ...] = Field(
        default=(), description="Candidates that missed at least one threshold."
    )


def load_pilot_manifest(path: Path | None = None) -> SarPilotManifest:
    """Load and strictly validate the committed, frozen Tier-3 pilot manifest."""
    manifest_path = path or DEFAULT_PILOT_MANIFEST
    try:
        raw: Any = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        return SarPilotManifest.model_validate(raw)
    except (OSError, TypeError, ValueError, yaml.YAMLError, ValidationError) as exc:
        raise SarPilotError(f"Tier-3 pilot manifest is invalid: {manifest_path}") from exc


def _clears(measurement: SarPilotMeasurement, thresholds: SarQualityThresholds) -> bool:
    """Return whether one candidate clears every committed acceptance threshold."""
    return (
        measurement.citation_precision >= thresholds.citation_precision_min
        and measurement.citation_recall >= thresholds.citation_recall_min
        and measurement.required_fact_coverage >= thresholds.required_fact_coverage_min
    )


def select_tier3_model(
    manifest: SarPilotManifest,
    measurements: tuple[SarPilotMeasurement, ...],
    thresholds: SarQualityThresholds,
) -> SarPilotSelection:
    """Apply the pre-registered rule: cheapest candidate clearing every threshold; ties broken."""
    declared = {candidate.model for candidate in manifest.candidates}
    measured = {measurement.model for measurement in measurements}
    if measured != declared:
        raise SarPilotError("Tier-3 pilot results do not cover exactly the declared candidates")
    if any(measurement.cases != len(manifest.cases) for measurement in measurements):
        raise SarPilotError("Tier-3 pilot candidates were not run over the identical case set")

    qualified = sorted(
        (item for item in measurements if _clears(item, thresholds)),
        key=lambda item: (item.cost_usd_per_1k_cases, item.model),
    )
    rejected = tuple(sorted(item.model for item in measurements if not _clears(item, thresholds)))
    if not qualified:
        return SarPilotSelection(
            reason="no candidate cleared every committed threshold; the cascade ships two tiers",
            rejected=rejected,
        )
    cheapest = qualified[0].cost_usd_per_1k_cases
    tied = [item.model for item in qualified if item.cost_usd_per_1k_cases == cheapest]
    winner = manifest.tie_break_model if manifest.tie_break_model in tied else qualified[0].model
    reason = (
        "tie on cost broken by the pre-registered tie-break model"
        if len(tied) > 1
        else "cheapest candidate clearing every committed threshold"
    )
    return SarPilotSelection(
        model=winner,
        reason=reason,
        qualified=tuple(item.model for item in qualified),
        rejected=rejected,
    )
