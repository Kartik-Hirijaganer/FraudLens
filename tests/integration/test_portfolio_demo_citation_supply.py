"""The citation supply the pinned story depends on, and what a rebuild does when it is missing.

The quality-gated SAR cascade (ADR-030) made a citation mandatory, which turned RAG retrieval —
a soft enhancer that degrades to empty everywhere else — into a hard precondition of a story that
pins five drafted SARs and no failed one. The deploy of 2026-09-18 found that the hard way, in
three separate respects:

  * the supply itself was never asserted, so nothing noticed that the bootstrap runs on the GitHub
    runner while the FinCEN/BSA index is baked into the container image;
  * `--reset` deleted the live tenant and only discovered halfway through the rebuild that no SAR
    could pass its gate, leaving the public demo half-built (rows scored, nothing triaged) with no
    rollback — scoring commits per run by design, so a doomed rebuild must not start at all;
  * the refusal it finally raised named the ALERT status ("only the pipeline's own alert-raise
    produces 'open'"), a symptom four links downstream of the gate-rejected SAR.

One section each, below. `test_portfolio_demo_citation_precondition.py` pins the chain that links
them, in isolation and without a database.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from decimal import Decimal
from pathlib import Path

import pytest
from rag_index import build_offline_rag_index
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.db.models import (
    Alert,
    AlertOrigin,
    AlertStatus,
    AnalysisRun,
    RunStatus,
    SarDraft,
    SarStatus,
    Severity,
    Transaction,
)
from fraudlens_backend.db.repositories import (
    AuditLogRepository,
    RuleRepository,
    TransactionRepository,
)
from fraudlens_backend.pipeline_wiring import build_pipeline_input
from fraudlens_backend.portfolio_demo import PortfolioDemoConfig, load_portfolio_demo_config
from fraudlens_backend.portfolio_demo.bootstrap import BootstrapRefusedError, preflight
from fraudlens_backend.portfolio_demo.bootstrap_workflow import apply_workflow_targets
from fraudlens_backend.portfolio_demo.ingest import ensure_story_transactions
from fraudlens_backend.rag import build_embedder
from fraudlens_backend.settings import AppSettings
from fraudlens_core import RuleRegistry
from fraudlens_ml.pipeline.steps import build_rag_query
from fraudlens_ml.rag import Retriever
from seed import seed  # scripts/ is on sys.path via conftest

_MODELS_DIR = Path(__file__).resolve().parents[2] / "data" / "models"


@pytest.fixture
def story() -> PortfolioDemoConfig:
    """Return the committed story the rebuild has to reproduce or refuse."""
    return load_portfolio_demo_config()


@pytest.fixture
def settings(
    make_settings: Callable[..., AppSettings], story: PortfolioDemoConfig, tmp_path: Path
) -> AppSettings:
    """Return settings on the story's provider modes, pointed at an isolated index directory."""
    return make_settings(
        llm_mode=story.execution.llm_mode,
        rag_embedding_mode=story.execution.rag_embedding_mode,
        model_artifacts_dir=str(_MODELS_DIR),
        rag_index_dir=str(tmp_path / "chroma"),
    )


@pytest.fixture
async def seeded(
    db_sessionmaker: async_sessionmaker[AsyncSession], settings: AppSettings
) -> AsyncIterator[AsyncSession]:
    """Yield a session over the seeded foundation (agency, personas, rules)."""
    async with db_sessionmaker() as session:
        await seed(session, settings)
        await session.commit()
        yield session


async def _count(session: AsyncSession, model: type[object]) -> int:
    """Return the row count for one model (the tenant is the only one that exists here)."""
    return int((await session.execute(select(func.count()).select_from(model))).scalar_one())  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------------------
# The supply itself: every SAR the story pins must have something to cite
# --------------------------------------------------------------------------------------------------


async def test_every_sar_bearing_scenario_retrieves_at_least_one_citation(
    seeded: AsyncSession, story: PortfolioDemoConfig, settings: AppSettings
) -> None:
    """Each story SAR must be offered a citation by the REAL query its own fired rules produce.

    This is the contract the fix rests on, and it is not self-evident: the query is built from rule
    reasons plus channel and country, scored against the configured similarity floor, so a reworded
    rule reason, a re-chunked corpus, or a nudged floor can silently starve one scenario while
    every other one still retrieves. `ring-payout-hop-3` is the thin one — it fires `rapid_movement`
    alone and clears the floor on a single chunk — so it is the row this test exists for.

    No model bundle is involved: rules decide the query, and the query decides the supply.
    """
    await ensure_story_transactions(seeded, story)
    await seeded.commit()
    retriever = Retriever(
        persist_dir=build_offline_rag_index(settings),
        collection=settings.rag_collection,
        embedder=build_embedder(settings),
        min_similarity=settings.investigation_rag_min_similarity,
    )
    definitions = await RuleRepository(seeded, story.agency.id).load_definitions()
    repo = TransactionRepository(seeded, story.agency.id)

    starved: list[str] = []
    for scenario in story.scenarios:
        if scenario.sar_target is None:
            continue
        transaction = await repo.get_by_external_id(story.external_id(scenario))
        assert transaction is not None
        pipeline_input = await build_pipeline_input(
            repo=repo,
            transaction=transaction,
            run_id=uuid.uuid4(),
            agency_id=story.agency.id,
            settings=settings,
        )
        evaluation = RuleRegistry().evaluate(definitions, pipeline_input.rule_context)
        query = build_rag_query(evaluation, pipeline_input)
        if not retriever.retrieve(query, top_k=settings.investigation_rag_top_k).chunks:
            starved.append(scenario.scenario_id)

    assert not starved, (
        f"{starved} would be offered no citation, so their SAR fails the gate terminally and the "
        "story's pinned SAR/alert states become unreachable"
    )


# --------------------------------------------------------------------------------------------------
# The destructive step must not start when the rebuild is already impossible
# --------------------------------------------------------------------------------------------------


async def test_an_absent_index_refuses_before_reset_deletes_the_live_story(
    seeded: AsyncSession, story: PortfolioDemoConfig, settings: AppSettings
) -> None:
    """`preflight` refuses while the live rows are still there, so `--reset` never runs.

    Scoring commits per run, so a rebuild cannot be rolled back once it starts — the only place a
    doomed rebuild can be stopped without cost is before the delete. This asserts on the ROWS, not
    on call order, because "nothing was destroyed" is the property that matters.
    """
    await ensure_story_transactions(seeded, story)
    await seeded.commit()
    before = await _count(seeded, Transaction)
    assert before == len(story.scenarios)

    with pytest.raises(BootstrapRefusedError, match="RAG index"):
        await preflight(seeded, story, settings, models_dir=_MODELS_DIR, reset=True)

    assert await _count(seeded, Transaction) == before


async def test_a_usable_index_lets_preflight_through_to_the_remaining_guards(
    seeded: AsyncSession, story: PortfolioDemoConfig, settings: AppSettings
) -> None:
    """With the index built, the RAG guard is silent — whatever refuses next is a different guard.

    Proves the new guard is a precondition check and not a blanket refusal: the only reason this
    may still raise is the pinned model bundle, which `.gitignore` does not track.
    """
    build_offline_rag_index(settings)
    try:
        await preflight(seeded, story, settings, models_dir=_MODELS_DIR, reset=True)
    except BootstrapRefusedError as refusal:
        assert "RAG index" not in str(refusal), refusal


# --------------------------------------------------------------------------------------------------
# The refusal names the gate, not the alert status it happens to produce
# --------------------------------------------------------------------------------------------------


async def _story_row_with_failed_sar(session: AsyncSession, story: PortfolioDemoConfig) -> None:
    """Give the first SAR-bearing scenario a completed run, a raised alert, and a FAILED draft."""
    scenario = next(item for item in story.scenarios if item.sar_target is not None)
    transaction = (
        await session.execute(
            select(Transaction).where(Transaction.external_id == story.external_id(scenario))
        )
    ).scalar_one()
    run = AnalysisRun(
        agency_id=story.agency.id,
        transaction_id=transaction.id,
        status=RunStatus.COMPLETED,
        model_version=story.model.version_label,
    )
    session.add(run)
    await session.flush()
    transaction.latest_run_id = run.id
    # Exactly what the pipeline persists for a gate-rejected draft: the alert carries the
    # `sar_unavailable` force-review flag, so it is raised `pending_review` rather than `open`.
    session.add(
        Alert(
            agency_id=story.agency.id,
            transaction_id=transaction.id,
            run_id=run.id,
            origin=AlertOrigin.PIPELINE,
            status=AlertStatus.PENDING_REVIEW,
            severity=Severity.HIGH,
            review_flags=[{"flag": "sar_unavailable", "reason": "synthetic test flag"}],
        )
    )
    session.add(
        SarDraft(
            agency_id=story.agency.id,
            run_id=run.id,
            model_id="mock",
            prompt_version="v-test",
            prompt_hash="0" * 64,
            status=SarStatus.FAILED,
            cost_usd=Decimal("0"),
        )
    )
    await session.commit()


async def test_a_gate_failed_sar_refuses_by_naming_the_gate_not_the_alert_status(
    seeded: AsyncSession, story: PortfolioDemoConfig
) -> None:
    """The message points at the rejected draft, which is the cause, not at the alert status.

    Before this, the first scenario's SAR target was applied (`failed` -> `rejected` is a legal
    review decision), and the run then died on the alert status with a message about what the
    pipeline's alert-raise produces — four links from anything an operator could act on.
    """
    await ensure_story_transactions(seeded, story)
    await seeded.commit()
    await _story_row_with_failed_sar(seeded, story)
    audit = AuditLogRepository(seeded, agency_id=story.agency.id, request_id=story.audit_request_id)

    with pytest.raises(BootstrapRefusedError) as refusal:
        await apply_workflow_targets(seeded, story, audit)

    message = str(refusal.value)
    assert "quality gate rejected it at every cascade tier" in message
    assert "alert-raise" not in message


async def test_the_failed_draft_is_left_exactly_as_the_pipeline_persisted_it(
    seeded: AsyncSession, story: PortfolioDemoConfig
) -> None:
    """The refusal is raised BEFORE the review decision, so no `failed` draft is quietly decided.

    A `failed` draft can legally be rejected, so without the new check the bootstrap would record
    a synthetic reviewer decision over a SAR that was never written — evidence of the real failure,
    overwritten by the story it prevented.
    """
    await ensure_story_transactions(seeded, story)
    await seeded.commit()
    await _story_row_with_failed_sar(seeded, story)
    audit = AuditLogRepository(seeded, agency_id=story.agency.id, request_id=story.audit_request_id)

    with pytest.raises(BootstrapRefusedError):
        await apply_workflow_targets(seeded, story, audit)

    # Read the LIVE session, deliberately without rolling back: a rollback would discard the
    # flushed decision too and the assertion would hold even with the check removed.
    drafts = (await seeded.execute(select(SarDraft))).scalars().all()
    assert [draft.status for draft in drafts] == [SarStatus.FAILED]
    assert all(draft.reviewed_by is None for draft in drafts)
