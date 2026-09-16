"""Contracts for the D4 daily LLM budget on the LIVE multi-agent drafting path.

`llm_daily_budget_usd` is the only bound on a public URL's OpenRouter spend, and it binds by being
resolved per tenant and threaded into the run-scoped drafter at construction time. The mock branch
of `build_pipeline_deps` is well covered elsewhere; this module covers the branch that can actually
spend money, and the two properties that make the cap trustworthy: a tenant can never raise its own
budget above what the deployment admits, and a tenant with no configured budget is denied rather
than treated as uncapped.

Live components are never built here — the factory is a stub that records what it was handed, so
these cases exercise the wiring with no provider key and no network.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tenancy import new_agency_id

from fraudlens_backend.db.models import (
    Agency,
    AnalysisRun,
    RunStatus,
    SystemConfig,
    Transaction,
)
from fraudlens_backend.pipeline_wiring import build_pipeline_components, build_pipeline_deps
from fraudlens_backend.sar.drafter_fallback import LiveAgentFallbackDrafter
from fraudlens_backend.settings import AppSettings
from fraudlens_ml.pipeline import StreamMessage
from fraudlens_ml.sar import SarInput, SarStreamEvent

_NOW = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
_CEILING = 0.25


class _StubDrafter:
    """A drafter that is not the mock one, so the live fallback guard accepts it."""

    async def draft(self, sar_input: SarInput) -> AsyncIterator[SarStreamEvent]:
        """Yield nothing; these cases assert wiring, never drafting behavior."""
        return
        yield  # pragma: no cover - unreachable, present to make this an async generator


def _transaction(agency_id: uuid.UUID, external_id: str) -> Transaction:
    """A masked transaction row, stored the way persistence already masks it."""
    return Transaction(
        agency_id=agency_id,
        external_id=external_id,
        amount=Decimal("9500.00"),
        currency="USD",
        occurred_at=_NOW,
        origin_account="****1111",
        dest_account="****2222",
        channel="wire",
        country="US",
        features={},
        feature_hash="h",
    )


async def _capture_budget(
    make_settings: Callable[..., AppSettings],
    db_sessionmaker: async_sessionmaker[AsyncSession],
    *,
    slug: str,
    configured_budget: float | None,
) -> tuple[dict[str, object], object]:
    """Build live deps against a stub factory and return what the factory was handed."""
    captured: dict[str, object] = {}
    components = build_pipeline_components(make_settings(llm_mode="mock"))

    def factory(_toolset: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return _StubDrafter()

    # A non-mock single writer: `LiveAgentFallbackDrafter` refuses a mock fallback outright, which
    # is the guard keeping a keyless drafter out of a live multi-agent run.
    live_components = replace(components, agent_drafter_factory=factory, drafter=_StubDrafter())
    settings = make_settings(
        llm_mode="live", multi_agent_sar_enabled=True, llm_daily_budget_usd=_CEILING
    )
    async with db_sessionmaker() as session:
        agency_id = new_agency_id()
        session.add(Agency(id=agency_id, name="Live", slug=slug))
        transaction = _transaction(agency_id, f"{slug}-1")
        session.add(transaction)
        await session.flush()
        run = AnalysisRun(
            agency_id=agency_id,
            transaction_id=transaction.id,
            status=RunStatus.RUNNING,
            workflow_mode="multi_agent",
            graph_version=components.agent_config.graph_version,
        )
        session.add(run)
        if configured_budget is not None:
            session.add(
                SystemConfig(agency_id=agency_id, key="llmDailyBudgetUsd", value=configured_budget)
            )
        await session.commit()

        async def emit(_message: StreamMessage) -> None:
            return None

        deps = await build_pipeline_deps(
            components=live_components,
            session=session,
            sessionmaker=db_sessionmaker,
            settings=settings,
            agency_id=agency_id,
            run_id=run.id,
            transaction_id=transaction.id,
            workflow_mode="multi_agent",
            emit=emit,
        )
    return captured, deps.drafter


async def test_a_tenant_cannot_raise_its_budget_above_what_the_deployment_admits(
    make_settings: Callable[..., AppSettings],
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The tenant asks for a dollar a day; config/prod.yaml admits a quarter of that."""
    captured, drafter = await _capture_budget(
        make_settings, db_sessionmaker, slug="live-agent-budget", configured_budget=1.0
    )

    assert captured["daily_limit_usd"] == Decimal(str(_CEILING))
    # Nothing has been drafted today, so the guard starts from zero rather than from None.
    assert captured["daily_spent_usd"] == Decimal("0")
    # agents.yml sets fallback_to_single_writer, so the primary is wrapped rather than returned.
    assert isinstance(drafter, LiveAgentFallbackDrafter)


async def test_a_tenant_with_no_configured_budget_is_denied_rather_than_uncapped(
    make_settings: Callable[..., AppSettings],
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """A missing `system_config` row means zero spend, not unlimited spend — D4 fails closed."""
    captured, _ = await _capture_budget(
        make_settings, db_sessionmaker, slug="unbudgeted-agent", configured_budget=None
    )

    assert captured["daily_limit_usd"] == Decimal("0")


def test_a_keyless_build_exposes_no_live_agent_drafter_factory(
    make_settings: Callable[..., AppSettings],
) -> None:
    """Without a provider key there is no factory at all, so the live branch cannot be entered."""
    components = build_pipeline_components(make_settings(llm_mode="mock"))
    assert components.agent_drafter_factory is None
