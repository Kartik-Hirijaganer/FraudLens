"""Summary: Tenant workflow policy, investigation input, and scoring-pointer resolution.

Key classes:
- (none)

Key functions:
- resolve_workflow_mode: apply settings and tenant feature gates.
- load_risk_policy: resolve the safe risk-scoring policy.
- build_pipeline_input: construct PHI-free pipeline input with bounded history.
- resolve_scoring_pointer: select active, override, candidate, or canary deployment.

Notes:
- Every database lookup is scoped by agency_id where the underlying data is tenant-owned.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import SystemConfig, Transaction
from fraudlens_backend.db.repositories import (
    ModelRegistryRepository,
    TransactionRepository,
    load_feature_flags,
)
from fraudlens_backend.settings import AppSettings
from fraudlens_core import (
    RiskBand,
    RiskPolicy,
    RuleContext,
    RuleTransaction,
    TransactionDirection,
)
from fraudlens_ml.pipeline import PipelineInput
from fraudlens_ml.scoring import CanaryRouter, DeploymentPointer

_RISK_BAND_THRESHOLDS_KEY = "riskBandThresholds"
_ALERT_THRESHOLD_KEY = "alertThreshold"
_RISK_BLEND_MODEL_WEIGHT_KEY = "riskBlendModelWeight"


async def resolve_workflow_mode(
    session: AsyncSession,
    *,
    settings: AppSettings,
    agency_id: uuid.UUID,
    requested: str | None = None,
) -> str:
    """Resolve workflow selection through settings AND tenant flags, failing closed."""
    flags = await load_feature_flags(session, agency_id=agency_id)
    enabled = settings.multi_agent_sar_enabled and flags.multi_agent_sar
    if requested == "single_writer":
        return "single_writer"
    return "multi_agent" if enabled else "single_writer"


async def load_risk_policy(session: AsyncSession) -> RiskPolicy:
    """Resolve the `RiskPolicy` from global `system_config`, falling back to the core defaults."""
    default = RiskPolicy()
    try:
        stmt = select(SystemConfig).where(
            SystemConfig.agency_id.is_(None),
            SystemConfig.key.in_(
                [
                    _RISK_BAND_THRESHOLDS_KEY,
                    _ALERT_THRESHOLD_KEY,
                    _RISK_BLEND_MODEL_WEIGHT_KEY,
                ]
            ),
        )
        rows = {row.key: row.value for row in (await session.execute(stmt)).scalars().all()}
    except Exception:  # DB hiccup → safe cached in-process defaults (plan §9.1)
        return default
    thresholds = _parse_thresholds(rows.get(_RISK_BAND_THRESHOLDS_KEY), default.band_thresholds)
    alert_threshold = _parse_float(rows.get(_ALERT_THRESHOLD_KEY), default.alert_threshold)
    model_weight = _parse_float(rows.get(_RISK_BLEND_MODEL_WEIGHT_KEY), default.model_weight)
    return RiskPolicy(
        model_weight=model_weight,
        band_thresholds=thresholds,
        alert_threshold=alert_threshold,
    )


def _parse_thresholds(raw: Any, fallback: dict[RiskBand, float]) -> dict[RiskBand, float]:
    """Parse a `{band: lower}` config map into typed thresholds (fallback on any bad value)."""
    if not isinstance(raw, dict):
        return dict(fallback)
    parsed: dict[RiskBand, float] = {}
    for key, value in raw.items():
        try:
            parsed[RiskBand(str(key))] = float(value)
        except (ValueError, TypeError):
            return dict(fallback)
    return parsed or dict(fallback)


def _parse_float(raw: Any, fallback: float) -> float:
    """Coerce a config value to float, falling back when absent or un-coercible."""
    try:
        return float(raw) if raw is not None else fallback
    except (ValueError, TypeError):
        return fallback


def _to_rule_transaction(transaction: Transaction, *, account: str) -> RuleTransaction:
    """Project a persisted transaction onto a PHI-free RuleTransaction with its direction."""
    direction = (
        TransactionDirection.OUTBOUND
        if transaction.origin_account == account
        else TransactionDirection.INBOUND
    )
    return RuleTransaction(
        amount=transaction.amount,
        currency=transaction.currency,
        country=transaction.country,
        channel=transaction.channel,
        occurred_at=transaction.occurred_at,
        direction=direction,
    )


async def build_pipeline_input(
    *,
    repo: TransactionRepository,
    transaction: Transaction,
    run_id: uuid.UUID,
    agency_id: uuid.UUID,
    settings: AppSettings,
) -> PipelineInput:
    """Assemble the PHI-free PipelineInput from a transaction + its windowed account histories.

    The origin-account history feeds the rules engine + feature extractor (each filters to its
    own window); the destination-account history (directions relative to the destination) feeds
    the v2 counterparty fan-in features only — both use the same window/cap settings so training
    can mirror exactly what scoring sees.
    """
    history_rows = await repo.same_account_history(
        account=transaction.origin_account,
        before=transaction.occurred_at,
        window_hours=settings.investigation_history_window_hours,
        limit=settings.investigation_history_max,
    )
    history = tuple(
        _to_rule_transaction(row, account=transaction.origin_account) for row in history_rows
    )
    counterparty_rows = await repo.same_account_history(
        account=transaction.dest_account,
        before=transaction.occurred_at,
        window_hours=settings.investigation_history_window_hours,
        limit=settings.investigation_history_max,
    )
    counterparty_history = tuple(
        _to_rule_transaction(row, account=transaction.dest_account) for row in counterparty_rows
    )
    current = RuleTransaction(
        amount=transaction.amount,
        currency=transaction.currency,
        country=transaction.country,
        channel=transaction.channel,
        occurred_at=transaction.occurred_at,
        direction=TransactionDirection.OUTBOUND,
    )
    return PipelineInput(
        agency_id=str(agency_id),
        run_id=str(run_id),
        transaction_id=str(transaction.id),
        source=transaction.source.value,
        rule_context=RuleContext(
            transaction=current,
            history=history,
            counterparty_history=counterparty_history,
        ),
        amount=transaction.amount,
        currency=transaction.currency,
        country=transaction.country,
        channel=transaction.channel,
        feature_hash=transaction.feature_hash,
    )


async def resolve_scoring_pointer(
    registry: ModelRegistryRepository,
    *,
    routing_key: str,
    model_override: str | None = None,
    allow_candidate_fallback: bool = False,
) -> tuple[DeploymentPointer | None, bool]:
    """Resolve the per-run scoring pointer + whether it routed to the canary (plan §10.5 / §5.4).

    `model_override` (a registered version label) takes precedence over everything: the run scores
    with exactly that version (the active model is its last-known-good fallback) and `was_canary` is
    False — it is an explicit operator choice, not a canary-routing decision. Absent: with no canary
    configured (or 0% / unresolved) this is the active pointer (+ previous active for fallback,
    unchanged from v1); when a canary rollout is live, `CanaryRouter` decides by a stable hash of
    `routing_key` (the transaction id) whether this run scores with the canary (its inference log
    then records the canary arm). Routing is deterministic, so a re-run / replay routes identically.
    """
    pointer = await registry.build_pointer()
    if model_override is not None:
        version = await registry.get_version_by_label(model_override)
        if version is None:  # the API validates existence first; defensive fallthrough to active
            return pointer, False
        overridden = DeploymentPointer(
            active_version_label=version.version_label,
            active_artifact_uri=version.artifact_uri,
            previous_version_label=pointer.active_version_label if pointer is not None else None,
            previous_artifact_uri=pointer.active_artifact_uri if pointer is not None else None,
        )
        return overridden, False
    if pointer is None:
        candidate = (
            await registry.build_latest_candidate_pointer() if allow_candidate_fallback else None
        )
        return candidate, False
    canary = await registry.build_canary_deployment()
    if canary is None:
        return pointer, False
    decision = CanaryRouter().route(canary, routing_key)
    if not decision.was_canary:
        return pointer, False
    routed = DeploymentPointer(
        active_version_label=decision.version_label,
        active_artifact_uri=decision.artifact_uri,
        previous_version_label=canary.active_version_label,
        previous_artifact_uri=canary.active_artifact_uri,
    )
    return routed, True
