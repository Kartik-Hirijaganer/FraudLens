"""Summary: The calibration probe behind `bootstrap_portfolio_demo.py --probe` (plan §16 Phase 6).
Calibration is a HUMAN step: the probe reports what the real pipeline actually produces for each
authored scenario so an operator can pin `expected:` by hand, and it never writes an expectation
back into the config. It resolves the live policy first — the blend weight, every band lower bound,
the alert threshold (all from `load_risk_policy`), `review_low_confidence_margin`, and the rules
denominator computed by summing the ENABLED `aml_rules` weights — and prints it as a header, so a
config change is visible in the report instead of silently redefining a pinned expectation. Then,
per candidate, it reports the calibrated probability `p`, the rules subscore `r`, the fired rule
codes, the blended score, the resolved band, and the review flags the alert would carry. The report
holds no numeric literal: every boundary comes from the policy or the config.

Key classes:
- ProbeCandidate: one scenario's achieved p / r / codes / combined / band / flags.
- ProbePolicy: the resolved policy header (blend, bounds, threshold, margin, rules denominator).
- ProbeReport: the header, every candidate, the achieved distribution, and the `expected:` block.

Key functions:
- probe_story: score every candidate in memory and build the report (persists no run).
- render_probe_report: render the report as the operator-facing text the CLI prints.

Notes:
- Probing INGESTS the configured rows (idempotently, with the same feature-hash drift guard the
  bootstrap uses) because the rules engine windows same-account history out of `transactions`; it
  writes nothing else — no `analysis_runs`, no alerts, no SAR drafts, no band on any row.
- Scoring routes through the CONFIGURED bundle label rather than the active deployment pointer, so
  the probe works on a database whose model has not been promoted yet. The active label is reported
  in the header so a divergence is visible.
- `probe.report_top_n` bounds how many fired rule codes are printed per row, keeping a pathological
  rule set from turning the report into a wall of text.
- The rendered `expected:` block is deliberately paste-ready YAML: pinning is a reviewed human
  commit, and hand-transcribing counts is how a distribution silently goes stale.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.repositories import (
    ModelRegistryRepository,
    RuleRepository,
    TransactionRepository,
)
from fraudlens_backend.db.repositories.alerts import compute_review_flags
from fraudlens_backend.pipeline_wiring import build_pipeline_input, load_risk_policy
from fraudlens_backend.portfolio_demo.config import PortfolioDemoConfig
from fraudlens_backend.portfolio_demo.ingest import ensure_story_transactions
from fraudlens_backend.portfolio_demo.verification import UNSCORED_LABEL
from fraudlens_backend.settings import AppSettings
from fraudlens_core import RiskBand, RuleRegistry
from fraudlens_ml.scoring.artifacts import DeploymentPointer, ModelCache
from fraudlens_ml.scoring.scorer import Scorer

_MISSING = "missing"
_NONE = "none"
_INDENT = "  "


class ProbePolicy(BaseModel):
    """The live policy the probe resolved, printed so a config change is visible in the report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_weight: float = Field(..., description="Blend weight on the model probability.")
    band_thresholds: dict[RiskBand, float] = Field(
        ..., description="Per-band cumulative lower bounds resolved from `system_config`."
    )
    alert_threshold: float = Field(..., description="Combined score at/above which a run alerts.")
    low_confidence_margin: float = Field(
        ..., description="Half-width around 0.5 that trips `low_model_confidence`."
    )
    rules_denominator: Decimal = Field(
        ..., description="Summed weight of the ENABLED `aml_rules` — the `r` denominator."
    )
    enabled_rule_codes: tuple[str, ...] = Field(
        ..., description="Codes contributing to that denominator, in evaluation order."
    )
    configured_model_label: str = Field(..., description="Bundle label the probe scored through.")
    active_model_label: str = Field(
        ..., description="Label the deployment pointer currently holds."
    )


class ProbeCandidate(BaseModel):
    """One candidate's achieved scoring outcome (the row an operator calibrates against)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: str = Field(..., description="Story key of the probed scenario.")
    external_id: str = Field(..., description="Derived transaction external id.")
    fraud_probability: float = Field(..., description="Calibrated model probability `p`.")
    rules_subscore: Decimal = Field(..., description="Weighted rules subscore `r` in [0, 1].")
    fired_codes: tuple[str, ...] = Field(..., description="Rule codes that actually fired.")
    combined_score: float = Field(..., description="Blended score the bands are resolved from.")
    risk_band: RiskBand = Field(..., description="Band the live policy resolves for this row.")
    alerts: bool = Field(..., description="Whether the combined score reaches the alert threshold.")
    review_flags: tuple[str, ...] = Field(
        ..., description="Force-review flag keys the raised alert would carry."
    )
    expected_risk_band: RiskBand | None = Field(
        default=None, description="Band the committed story currently pins, for side-by-side."
    )


class ProbeReport(BaseModel):
    """A whole probe pass: the resolved policy, every candidate, and the achieved distribution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    story_version: str = Field(..., description="Story revision the candidates were read from.")
    policy: ProbePolicy = Field(..., description="The resolved policy header.")
    candidates: tuple[ProbeCandidate, ...] = Field(..., description="One row per scored scenario.")
    unscored: int = Field(..., ge=0, description="Rows deliberately held back from scoring.")
    report_top_n: int = Field(..., gt=0, description="How many fired codes are printed per row.")

    @property
    def achieved_bands(self) -> dict[RiskBand, int]:
        """Return the band distribution the probe actually achieved across the candidates."""
        counts: dict[RiskBand, int] = {}
        for candidate in self.candidates:
            counts[candidate.risk_band] = counts.get(candidate.risk_band, 0) + 1
        return counts

    @property
    def alerting(self) -> int:
        """Return how many candidates crossed the alert threshold (each produces one SAR draft)."""
        return sum(1 for candidate in self.candidates if candidate.alerts)


async def _rules_denominator(
    session: AsyncSession, config: PortfolioDemoConfig
) -> tuple[Decimal, tuple[str, ...]]:
    """Return the summed weight of the enabled effective rules plus their codes (the `r` base)."""
    definitions = await RuleRepository(session, config.agency.id).load_definitions()
    enabled = [definition for definition in definitions if definition.enabled]
    total = sum((definition.weight for definition in enabled), Decimal(0))
    return total, tuple(definition.code for definition in enabled)


async def _resolve_policy(
    session: AsyncSession, config: PortfolioDemoConfig, settings: AppSettings
) -> ProbePolicy:
    """Resolve every boundary the report prints from the live policy, settings, and the DB rules."""
    policy = await load_risk_policy(session)
    denominator, codes = await _rules_denominator(session, config)
    pointer = await ModelRegistryRepository(session).build_pointer()
    return ProbePolicy(
        model_weight=policy.model_weight,
        band_thresholds=dict(policy.band_thresholds),
        alert_threshold=policy.alert_threshold,
        low_confidence_margin=settings.review_low_confidence_margin,
        rules_denominator=denominator,
        enabled_rule_codes=codes,
        configured_model_label=config.model.version_label,
        active_model_label=_MISSING if pointer is None else pointer.active_version_label,
    )


async def probe_story(
    session: AsyncSession,
    config: PortfolioDemoConfig,
    settings: AppSettings,
    *,
    models_dir: Path,
) -> ProbeReport:
    """Score every candidate in memory against the pinned bundle and build the report (no runs)."""
    await ensure_story_transactions(session, config)
    await session.commit()

    probe_policy = await _resolve_policy(session, config, settings)
    risk_policy = await load_risk_policy(session)
    definitions = await RuleRepository(session, config.agency.id).load_definitions()
    repo = TransactionRepository(session, config.agency.id)
    scorer = Scorer(ModelCache(models_dir))
    pointer = DeploymentPointer(
        active_version_label=config.model.version_label,
        active_artifact_uri=config.model.version_label,
    )

    candidates: list[ProbeCandidate] = []
    for scenario in config.scored_scenarios:
        transaction = await repo.get_by_external_id(config.external_id(scenario))
        if transaction is None:  # pragma: no cover - the ingest above guarantees the row
            raise RuntimeError(f"scenario '{scenario.scenario_id}' was not ingested")
        pipeline_input = await build_pipeline_input(
            repo=repo,
            transaction=transaction,
            run_id=uuid.uuid4(),
            agency_id=config.agency.id,
            settings=settings,
        )
        evaluation = RuleRegistry().evaluate(definitions, pipeline_input.rule_context)
        score = scorer.score(pointer, pipeline_input.rule_context)
        assessment = risk_policy.assess(
            fraud_probability=score.fraud_probability,
            rules_subscore=evaluation.subscore,
            model_thresholds=score.risk_thresholds,
        )
        flags = compute_review_flags(
            risk_band=assessment.risk_band,
            fraud_probability=score.fraud_probability,
            sar_status=None,
            low_confidence_margin=settings.review_low_confidence_margin,
        )
        candidates.append(
            ProbeCandidate(
                scenario_id=scenario.scenario_id,
                external_id=config.external_id(scenario),
                fraud_probability=score.fraud_probability,
                rules_subscore=evaluation.subscore,
                fired_codes=tuple(sorted(hit.code for hit in evaluation.hits)),
                combined_score=assessment.combined_score,
                risk_band=assessment.risk_band,
                alerts=assessment.alert,
                review_flags=tuple(str(flag["flag"]) for flag in flags),
                expected_risk_band=scenario.expected_risk_band,
            )
        )
    return ProbeReport(
        story_version=config.story_version,
        policy=probe_policy,
        candidates=tuple(candidates),
        unscored=len(config.scenarios) - len(config.scored_scenarios),
        report_top_n=config.probe.report_top_n,
    )


def _render_policy(policy: ProbePolicy) -> list[str]:
    """Render the resolved-policy header lines (every boundary read, none restated)."""
    bounds = ", ".join(
        f"{band.value}={policy.band_thresholds[band]}"
        for band in RiskBand
        if band in policy.band_thresholds
    )
    return [
        "resolved policy",
        f"{_INDENT}blend model weight      : {policy.model_weight}",
        f"{_INDENT}band lower bounds       : {bounds}",
        f"{_INDENT}alert threshold         : {policy.alert_threshold}",
        f"{_INDENT}low-confidence margin   : {policy.low_confidence_margin}",
        f"{_INDENT}rules denominator       : {policy.rules_denominator} "
        f"({', '.join(policy.enabled_rule_codes) or _NONE})",
        f"{_INDENT}configured model        : {policy.configured_model_label}",
        f"{_INDENT}active model            : {policy.active_model_label}",
    ]


def _render_candidates(report: ProbeReport) -> list[str]:
    """Render one line per candidate: p, r, fired codes, combined, band, and review flags."""
    lines = ["", "candidates (p = calibrated probability, r = rules subscore)"]
    for candidate in report.candidates:
        codes = list(candidate.fired_codes)[: report.report_top_n]
        pinned = (
            candidate.expected_risk_band.value
            if candidate.expected_risk_band is not None
            else _NONE
        )
        lines.append(
            f"{_INDENT}{candidate.external_id}  p={candidate.fraud_probability:.6f}  "
            f"r={candidate.rules_subscore}  combined={candidate.combined_score:.4f}  "
            f"band={candidate.risk_band.value}  alert={candidate.alerts}  "
            f"pinned={pinned}"
        )
        lines.append(
            f"{_INDENT * 2}codes={','.join(codes) or _NONE}  "
            f"flags={','.join(candidate.review_flags) or _NONE}"
        )
    return lines


def _render_expected_block(report: ProbeReport) -> list[str]:
    """Render the paste-ready `expected:` YAML the operator pins by hand (never written here)."""
    achieved = report.achieved_bands
    lines = [
        "",
        "paste-ready expected block (pin it by hand — the probe never writes config)",
        "expected:",
        f"{_INDENT}transactions: {len(report.candidates) + report.unscored}",
        f"{_INDENT}unscored: {report.unscored}",
        f"{_INDENT}risk_bands:",
    ]
    lines.extend(
        f"{_INDENT * 2}{band.value}: {achieved[band]}" for band in RiskBand if band in achieved
    )
    lines.append(
        f"{_INDENT}# {report.alerting} alerting row(s) -> {report.alerting} SAR draft(s); "
        "distribute them across alert_states / sar_states with the configured targets."
    )
    return lines


def render_probe_report(report: ProbeReport) -> str:
    """Render the whole probe report as the text the CLI prints (PHI-free, literal-free)."""
    lines = [f"portfolio demo probe — story {report.story_version}", ""]
    lines.extend(_render_policy(report.policy))
    lines.extend(_render_candidates(report))
    lines.extend(_render_expected_block(report))
    lines.append("")
    lines.append(f"{UNSCORED_LABEL} rows held for a live investigation: {report.unscored}")
    return "\n".join(lines)
