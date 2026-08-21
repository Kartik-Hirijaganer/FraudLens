"""Summary: The SAR-draft regeneration service (analyst "Regenerate" action on the investigation
screen). It re-drafts the SAR for a *completed* run WITHOUT re-running scoring/RAG: it reconstructs
the PHI-free `SarInput` from the run's already-persisted evidence — the immutable `analysis_results`
snapshot (fraud probability, risk band, rule hits, top SHAP features), the transaction's non-PHI
structured facts, and the grounded citations carried on the latest `sar_drafts` row — then invokes
the same injected `SarDrafter` (mock or live) the pipeline uses and persists the terminal result as
the next `sar_drafts` version via `create_from_result`. So a regenerate is auditable and versioned
(never overwrites the prior draft), reuses one drafting path (rule 5), and stays agency-scoped.

Key classes:
- (none)

Key functions:
- sar_draft_to_view: project a persisted `SarDraft` ORM row onto the camelCase `SarDraftView`.
- regenerate_sar_for_run: reconstruct the input, draft, and persist the next SAR version for a run.

Notes:
- `rag_context` is intentionally left empty on the reconstructed input: rebuilding the fenced
  regulation block would import the chromadb-backed RAG retriever into the request path, and it is
  not needed for grounding — the drafter grounds `cited_regulations` against `SarInput.citations`
  (protocol §8.1), which are reconstructed from the prior draft. The keyless mock drafter (the
  local-demo / no-key default) composes purely from those citations.
- A decided draft (approved/rejected) is not regenerable (`invalid_sar_transition`, 409) — a
  regenerate must not discard a recorded human decision. Regeneration also requires a completed run
  with an `analysis_results` snapshot to reconstruct from (`sar_not_regenerable`, 409).
"""

from __future__ import annotations

import uuid

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import SarDraft
from fraudlens_backend.db.models.enums import SarStatus
from fraudlens_backend.db.repositories import (
    AnalysisRunRepository,
    SarDraftRepository,
    TransactionRepository,
)
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.models.sar import SarDraftView
from fraudlens_backend.sar import build_sar_drafter
from fraudlens_backend.settings import AppSettings
from fraudlens_backend.telemetry import log_llm_call
from fraudlens_core.rules.base import RuleHit
from fraudlens_ml.sar import (
    SarCitation,
    SarDrafter,
    SarDraftResult,
    SarEventType,
    SarFeature,
    SarInput,
)

_TERMINAL_EVENTS = frozenset({SarEventType.COMPLETED, SarEventType.FAILED})

# SAR statuses from which a draft is decided — a regenerate would discard a human decision (409).
_DECIDED: frozenset[SarStatus] = frozenset({SarStatus.APPROVED, SarStatus.REJECTED})


def sar_draft_to_view(draft: SarDraft) -> SarDraftView:
    """Project a persisted SAR draft row onto the API view (the single ORM→view mapping, rule 5)."""
    return SarDraftView(
        sar_draft_id=str(draft.id),
        run_id=str(draft.run_id),
        alert_id=str(draft.alert_id) if draft.alert_id is not None else None,
        version=draft.version,
        status=draft.status,
        content=draft.content,
        structured=dict(draft.structured or {}),
        citations=[dict(citation) for citation in (draft.citations or [])],
        model_id=draft.model_id,
        prompt_version=draft.prompt_version,
        prompt_hash=draft.prompt_hash,
        workflow=draft.workflow,
        revision_count=draft.revision_count,
        token_usage=dict(draft.token_usage or {}),
        cost_usd=draft.cost_usd,
        created_at=draft.created_at,
    )


def _rule_hits(raw: list[object]) -> tuple[RuleHit, ...]:
    """Rebuild the PHI-free `RuleHit`s from stored camelCase JSON (`ruleType` → `rule_type`).

    A stored hit that can't be revalidated (e.g. a partial legacy row) is skipped rather than
    failing the whole regeneration — degrade safely, never 500 on one malformed evidence row.
    """
    hits: list[RuleHit] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        data = dict(item)
        if "ruleType" in data:
            data["rule_type"] = data.pop("ruleType")
        try:
            hits.append(RuleHit.model_validate(data))
        except ValidationError:
            continue
    return tuple(hits)


def _features(raw: list[object]) -> tuple[SarFeature, ...]:
    """Reconstruct the top SHAP drivers from stored JSON (feature/value/shapValue), skipping bad."""
    features: list[SarFeature] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            features.append(SarFeature.model_validate(item))
        except ValidationError:
            continue
    return tuple(features)


def _citations(draft: SarDraft | None) -> tuple[SarCitation, ...]:
    """Reconstruct the grounded citations from the latest draft's stored citation JSON."""
    if draft is None:
        return ()
    citations: list[SarCitation] = []
    for item in draft.citations or []:
        if not isinstance(item, dict):
            continue
        try:
            citations.append(SarCitation.model_validate(item))
        except ValidationError:
            continue
    return tuple(citations)


async def _draft_result(drafter: SarDrafter, sar_input: SarInput) -> SarDraftResult:
    """Consume the drafter's token stream and return its terminal (completed/failed) result."""
    async for event in drafter.draft(sar_input):
        if event.type in _TERMINAL_EVENTS and event.result is not None:
            return event.result
    # A well-behaved drafter always yields a terminal event; treat its absence as non-regenerable.
    raise AppError("sar_not_regenerable")


async def regenerate_sar_for_run(  # noqa: PLR0913 - explicit DI collaborators + scope (keyword-only).
    *,
    session: AsyncSession,
    agency_id: uuid.UUID,
    run_id: uuid.UUID,
    settings: AppSettings,
    drafter: SarDrafter | None = None,
    created_by: uuid.UUID | None = None,
) -> SarDraft:
    """Re-draft the SAR for a completed run and persist it as the next version (agency-scoped).

    Raises `investigation_not_found` (unknown/cross-tenant run), `transaction_not_found` (the run's
    transaction is gone), `sar_not_regenerable` (no completed `analysis_results` to draft from), or
    `invalid_sar_transition` (the latest draft is approved/rejected — a decided SAR is not
    regenerable). The returned draft is flushed but NOT committed — the caller commits after writing
    its audit row.
    """
    run_repo = AnalysisRunRepository(session, agency_id)
    run = await run_repo.get(run_id)
    if run is None:
        raise AppError("investigation_not_found")
    result = await run_repo.get_result(run_id)
    if result is None:
        raise AppError("sar_not_regenerable")
    transaction = await TransactionRepository(session, agency_id).get(run.transaction_id)
    if transaction is None:
        raise AppError("transaction_not_found")
    sar_repo = SarDraftRepository(session, agency_id)
    latest = await sar_repo.get_for_run(run_id)
    if latest is not None and latest.status in _DECIDED:
        # An approved/rejected draft is a recorded human decision — do not overwrite it (§10.4).
        raise AppError("invalid_sar_transition")
    sar_input = SarInput(
        agency_id=str(agency_id),
        transaction_id=str(run.transaction_id),
        risk_band=result.risk_band,
        fraud_probability=result.fraud_probability,
        amount=transaction.amount,
        currency=transaction.currency,
        country=transaction.country,
        channel=transaction.channel,
        model_version=result.model_version,
        rules_version=run.rules_version or result.model_version,
        rag_version=run.rag_version or result.model_version,
        rule_hits=_rule_hits(list(result.rule_hits or [])),
        top_features=_features(list(result.top_features or [])),
        citations=_citations(latest),
        rag_context="",
    )
    active_drafter = drafter if drafter is not None else build_sar_drafter(settings)
    draft_result = await _draft_result(active_drafter, sar_input)
    draft = await sar_repo.create_from_result(
        run_id=run_id,
        result=draft_result,
        alert_id=latest.alert_id if latest is not None else None,
        created_by=created_by,
    )
    log_llm_call(
        model=draft_result.model_id,
        prompt_version=draft_result.prompt_version,
        prompt_hash=draft_result.prompt_hash,
        input_tokens=draft_result.token_usage.input_tokens,
        output_tokens=draft_result.token_usage.output_tokens,
        total_tokens=draft_result.token_usage.total_tokens,
        cost_usd=draft_result.cost_usd,
        fallback_count=draft_result.fallback_count,
        cached=draft_result.cached,
        run_id=str(run_id),
        agency_id=str(agency_id),
    )
    return draft
