"""Cross-replica multi-agent spend admission and tenant-isolation tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.db.models import Agency, AnalysisRun, RunStatus, SystemConfig, Transaction
from fraudlens_backend.db.repositories import AnalysisRunRepository
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.runs.admission import reserve_agent_spend

# Above every tenant budget these tests seed, so the tenant value is what binds here; the
# ceiling's own behavior is exercised separately below.
_UNBINDING_CEILING = Decimal("1.00")


async def _seed_runs(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID, tuple[uuid.UUID, uuid.UUID, uuid.UUID]]:
    """Seed two active runs for one tenant and one run for another tenant."""
    primary_id = uuid.uuid4()
    other_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with sessionmaker() as session:
        session.add_all(
            [
                Agency(id=primary_id, name="Primary", slug=f"primary-{primary_id.hex}"),
                Agency(id=other_id, name="Other", slug=f"other-{other_id.hex}"),
                SystemConfig(agency_id=primary_id, key="llmDailyBudgetUsd", value=0.9),
                SystemConfig(agency_id=other_id, key="llmDailyBudgetUsd", value=0.9),
            ]
        )
        run_ids: list[uuid.UUID] = []
        for index, agency_id in enumerate((primary_id, primary_id, other_id)):
            transaction = Transaction(
                agency_id=agency_id,
                external_id=f"admission-{index}-{uuid.uuid4().hex}",
                amount=Decimal("100.00"),
                currency="USD",
                occurred_at=now,
                origin_account="masked-origin",
                dest_account="masked-destination",
                channel="wire",
                country="US",
                features={},
                feature_hash="f" * 64,
            )
            session.add(transaction)
            await session.flush()
            run = await AnalysisRunRepository(session, agency_id).create_pending(
                transaction_id=transaction.id,
                deadline_at=now + timedelta(minutes=5),
                request_fingerprint=f"{index}" * 64,
            )
            run_ids.append(run.id)
        await session.commit()
    return primary_id, other_id, (run_ids[0], run_ids[1], run_ids[2])


async def test_reservations_deny_overcommit_and_remain_tenant_scoped(db_sessionmaker) -> None:
    primary_id, other_id, run_ids = await _seed_runs(db_sessionmaker)

    async with db_sessionmaker() as session:
        first = await AnalysisRunRepository(session, primary_id).get(run_ids[0])
        assert first is not None
        reserved = await reserve_agent_spend(
            session,
            run=first,
            agency_id=primary_id,
            daily_budget_ceiling_usd=_UNBINDING_CEILING,
            maximum_attempt_cost_usd=Decimal("0.6"),
        )
        await session.commit()
    assert reserved == Decimal("0.6")

    async with db_sessionmaker() as session:
        second = await AnalysisRunRepository(session, primary_id).get(run_ids[1])
        assert second is not None
        with pytest.raises(AppError, match="llm_budget_exceeded"):
            await reserve_agent_spend(
                session,
                run=second,
                agency_id=primary_id,
                daily_budget_ceiling_usd=_UNBINDING_CEILING,
                maximum_attempt_cost_usd=Decimal("0.6"),
            )
        await session.rollback()

    async with db_sessionmaker() as session:
        other = await AnalysisRunRepository(session, other_id).get(run_ids[2])
        assert other is not None
        await reserve_agent_spend(
            session,
            run=other,
            agency_id=other_id,
            daily_budget_ceiling_usd=_UNBINDING_CEILING,
            maximum_attempt_cost_usd=Decimal("0.6"),
        )
        await session.commit()
        assert other.llm_reserved_usd == Decimal("0.6")

    async with db_sessionmaker() as session:
        first = await AnalysisRunRepository(session, primary_id).get(run_ids[0])
        second = await AnalysisRunRepository(session, primary_id).get(run_ids[1])
        assert first is not None and second is not None
        first.status = RunStatus.COMPLETED
        await reserve_agent_spend(
            session,
            run=second,
            agency_id=primary_id,
            daily_budget_ceiling_usd=_UNBINDING_CEILING,
            maximum_attempt_cost_usd=Decimal("0.6"),
        )
        await session.commit()
        assert second.llm_reserved_usd == Decimal("0.6")


async def test_reservation_fails_closed_for_mismatched_or_missing_tenant(db_sessionmaker) -> None:
    primary_id, other_id, run_ids = await _seed_runs(db_sessionmaker)

    async with db_sessionmaker() as session:
        run = await AnalysisRunRepository(session, primary_id).get(run_ids[0])
        assert run is not None
        with pytest.raises(AppError, match="llm_budget_exceeded"):
            await reserve_agent_spend(
                session,
                run=run,
                agency_id=other_id,
                daily_budget_ceiling_usd=_UNBINDING_CEILING,
                maximum_attempt_cost_usd=Decimal("0.1"),
            )

        missing_id = uuid.uuid4()
        missing_run = AnalysisRun(
            agency_id=missing_id,
            transaction_id=uuid.uuid4(),
            status=RunStatus.PENDING,
        )
        with pytest.raises(AppError, match="llm_budget_exceeded"):
            await reserve_agent_spend(
                session,
                run=missing_run,
                agency_id=missing_id,
                daily_budget_ceiling_usd=_UNBINDING_CEILING,
                maximum_attempt_cost_usd=Decimal("0.1"),
            )


async def test_the_deployment_ceiling_binds_below_a_tenants_configured_budget(
    db_sessionmaker,
) -> None:
    # The tenant row says $0.90/day, but this deployment admits only $0.20, so a $0.60 worst case
    # is refused with the catalog code that renders the standard error envelope.
    primary_id, _other_id, run_ids = await _seed_runs(db_sessionmaker)

    async with db_sessionmaker() as session:
        run = await AnalysisRunRepository(session, primary_id).get(run_ids[0])
        assert run is not None
        with pytest.raises(AppError, match="llm_budget_exceeded"):
            await reserve_agent_spend(
                session,
                run=run,
                agency_id=primary_id,
                maximum_attempt_cost_usd=Decimal("0.60"),
                daily_budget_ceiling_usd=Decimal("0.20"),
            )
        await session.rollback()


async def test_a_reservation_at_the_ceiling_is_admitted_and_over_it_is_not(
    db_sessionmaker,
) -> None:
    primary_id, _other_id, run_ids = await _seed_runs(db_sessionmaker)

    async with db_sessionmaker() as session:
        run = await AnalysisRunRepository(session, primary_id).get(run_ids[0])
        assert run is not None
        reserved = await reserve_agent_spend(
            session,
            run=run,
            agency_id=primary_id,
            maximum_attempt_cost_usd=Decimal("0.25"),
            daily_budget_ceiling_usd=Decimal("0.25"),
        )
        await session.commit()
    assert reserved == Decimal("0.25")

    async with db_sessionmaker() as session:
        second = await AnalysisRunRepository(session, primary_id).get(run_ids[1])
        assert second is not None
        with pytest.raises(AppError, match="llm_budget_exceeded"):
            await reserve_agent_spend(
                session,
                run=second,
                agency_id=primary_id,
                maximum_attempt_cost_usd=Decimal("0.01"),
                daily_budget_ceiling_usd=Decimal("0.25"),
            )
        await session.rollback()
