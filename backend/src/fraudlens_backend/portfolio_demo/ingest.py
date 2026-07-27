"""Summary: The one path that puts the configured story's transactions into the database (plan §16
Phase 6). Both the bootstrap and `--probe` need the authored rows present — the probe because the
rules engine windows same-account history out of `transactions` — so the ingest lives here once
rather than in each (rule 5). Every payload goes through `build_canonical` (validation + amount
quantization + tz-aware occurrence) and then `TransactionRepository.ingest`, so accounts are masked
before any write and no PHI is persisted. Because `ingest` dedups on `external_id` and RETURNS the
existing row, an edited YAML amount would otherwise be silently ignored: each duplicate is therefore
re-hashed with `compute_feature_hash` and compared with the stored `feature_hash`, so content drift
fails loudly instead of leaving the database telling the old story.

Key classes:
- StoryIngestError: raised when an already-present row no longer matches its configured payload.
- StoryIngestReport: PHI-free counts plus the resolved scenario-id → transaction-id map.

Key functions:
- canonical_for: build the validated `CanonicalTransaction` for one configured scenario.
- ensure_story_transactions: idempotently ingest every scenario, failing on content drift.

Notes:
- Rows are ingested in CONFIGURED order, which is also anchor order (each scenario's
  `occurred_offset_hours` increases down the file), so a later row's history window already holds
  its predecessors exactly as it will at scoring time.
- Nothing is committed here; the caller owns the transaction boundary.
- The drift error names the scenario id only — never the amount, account, or hash — so a failure
  cannot echo an authored payload.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.repositories import TransactionRepository
from fraudlens_backend.portfolio_demo.config import PortfolioDemoConfig, PortfolioDemoScenario
from fraudlens_core import CanonicalTransaction, build_canonical, compute_feature_hash


class StoryIngestError(RuntimeError):
    """Raised when a persisted story row no longer matches its configured payload."""


class StoryIngestReport(BaseModel):
    """PHI-free outcome of one story ingest pass, with the ids the scorer needs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    created: int = Field(..., ge=0, description="Scenarios newly inserted by this pass.")
    existing: int = Field(..., ge=0, description="Scenarios already present and content-identical.")
    transaction_ids: dict[str, uuid.UUID] = Field(
        ..., description="scenario_id → persisted transaction id, for every configured scenario."
    )


def canonical_for(
    config: PortfolioDemoConfig, scenario: PortfolioDemoScenario
) -> CanonicalTransaction:
    """Return the validated canonical transaction for one configured scenario."""
    payload = scenario.transaction
    return build_canonical(
        external_id=config.external_id(scenario),
        amount=payload.amount,
        currency=payload.currency,
        occurred_at=config.occurred_at(scenario),
        origin_account=payload.origin_account,
        dest_account=payload.dest_account,
        channel=payload.channel,
        country=payload.country,
        features=dict(payload.features),
    )


async def ensure_story_transactions(
    session: AsyncSession, config: PortfolioDemoConfig
) -> StoryIngestReport:
    """Idempotently ingest every configured scenario; raise on content drift (no commit)."""
    repo = TransactionRepository(session, config.agency.id)
    ids: dict[str, uuid.UUID] = {}
    created = existing = 0
    for scenario in config.scenarios:
        canonical = canonical_for(config, scenario)
        outcome = await repo.ingest(canonical)
        if outcome.created:
            created += 1
        else:
            if outcome.transaction.feature_hash != compute_feature_hash(canonical):
                raise StoryIngestError(
                    f"scenario '{scenario.scenario_id}' is already stored under a different "
                    "payload (feature-hash drift) — reset the story before changing its content"
                )
            existing += 1
        ids[scenario.scenario_id] = outcome.transaction.id
    return StoryIngestReport(created=created, existing=existing, transaction_ids=ids)
