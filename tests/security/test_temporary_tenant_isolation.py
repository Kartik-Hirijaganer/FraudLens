"""Multi-tenant isolation proved with TEMPORARY tenants (plan §16 Phase 8, ADR-018).

Exactly one persistent portfolio-demo agency exists at runtime, so the old "sign in as a second
tenant and watch the data disappear" browser moment is gone. The invariant it demonstrated is not:
this suite mints two throwaway tenants per test and drives them through every layer that carries a
tenant scope — the repositories, the HTTP API, the batch job, the alert/SAR workflow service, and
the dashboard aggregate — asserting that neither can see, count, or address the other's rows.

Neither tenant is ever registered in runtime configuration: they exist only for the length of a
test, which is precisely why they can prove generic multi-tenancy without the portfolio story.

`test_tenant_isolation.py` covers the same invariant at the HTTP edge for the claim/path mismatch;
this file goes one layer down, to the objects that actually issue the queries.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tenancy import create_tenant

from fraudlens_backend.api.deps import TokenVerifier, get_token_verifier
from fraudlens_backend.db.models import (
    Agency,
    AlertActionType,
    AlertStatus,
    AnalysisRun,
    RunStatus,
    SarDraft,
    Severity,
    Transaction,
    User,
    UserRole,
)
from fraudlens_backend.db.repositories import (
    AlertRepository,
    AnalysisRunRepository,
    AuditLogRepository,
    DashboardRepository,
    SarDraftRepository,
    TransactionRepository,
)
from fraudlens_backend.jobs.runner import select_uninvestigated
from fraudlens_backend.models.alerts import SarReviewDecision
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.services.alert_workflow import (
    AlertActionCommand,
    AlertWorkflowService,
    SarReviewCommand,
)
from fraudlens_core import RiskBand

AUTH = {"Authorization": "Bearer test-token"}


class _Tenant:
    """One throwaway tenant plus the ids of the evidence trail created inside it."""

    def __init__(self, agency: Agency, user: User) -> None:
        """Record the tenant's agency/user; the row ids are filled in by `_populate`."""
        self.agency = agency
        self.user = user
        self.transaction_id: uuid.UUID
        self.run_id: uuid.UUID
        self.alert_id: uuid.UUID


async def _populate(session: AsyncSession, label: str) -> _Tenant:
    """Mint a tenant and give it a full transaction → run → alert → SAR-draft evidence trail."""
    agency, user = await create_tenant(session, label=label, role=UserRole.REVIEWER)
    tenant = _Tenant(agency, user)

    transaction = Transaction(
        agency_id=agency.id,
        external_id=f"{label}-txn",
        amount=Decimal("9100.00"),
        currency="USD",
        occurred_at=datetime.now(UTC),
        origin_account="********1111",
        dest_account="********2222",
        channel="ach",
        country="US",
        features={"dataset_source": "test-fixture"},
        feature_hash=f"{label:0<64}"[:64],
        risk_band=RiskBand.HIGH,
    )
    session.add(transaction)
    await session.flush()

    run = AnalysisRun(
        agency_id=agency.id,
        transaction_id=transaction.id,
        status=RunStatus.COMPLETED,
        model_version="v-test",
    )
    session.add(run)
    await session.flush()
    transaction.latest_run_id = run.id

    alert = await AnalysisRunRepository(session, agency.id).raise_alert(
        run_id=run.id, transaction_id=transaction.id, severity=Severity.HIGH, review_flags=[]
    )
    session.add(
        SarDraft(
            agency_id=agency.id,
            run_id=run.id,
            alert_id=alert.id,
            model_id="mock",
            prompt_version="sar-v1",
            prompt_hash="h",
            content=f"{label} synthetic narrative.",
            structured={},
            citations=[],
        )
    )
    await session.commit()

    tenant.transaction_id = transaction.id
    tenant.run_id = run.id
    tenant.alert_id = alert.id
    return tenant


@pytest.fixture
async def tenants(db_session: AsyncSession) -> tuple[_Tenant, _Tenant]:
    """Return two fully populated, mutually invisible throwaway tenants."""
    return await _populate(db_session, "alpha"), await _populate(db_session, "beta")


# --------------------------------------------------------------------------------------------------
# Repositories
# --------------------------------------------------------------------------------------------------


async def test_repositories_cannot_read_across_the_boundary(
    db_session: AsyncSession, tenants: tuple[_Tenant, _Tenant]
) -> None:
    alpha, beta = tenants

    transactions = TransactionRepository(db_session, alpha.agency.id)
    assert await transactions.get(alpha.transaction_id) is not None
    assert await transactions.get(beta.transaction_id) is None
    assert await transactions.get_by_external_id("beta-txn") is None

    alerts = AlertRepository(db_session, alpha.agency.id)
    assert await alerts.get(alpha.alert_id) is not None
    assert await alerts.get(beta.alert_id) is None
    assert await alerts.get_alert_summary(beta.alert_id) is None
    assert await alerts.get_for_run(beta.run_id) is None

    sar = SarDraftRepository(db_session, alpha.agency.id)
    assert await sar.get_for_run(alpha.run_id) is not None
    assert await sar.get_for_run(beta.run_id) is None
    assert list(await sar.list_for_alert(beta.alert_id)) == []


async def test_the_dashboard_aggregate_counts_only_its_own_tenant(
    db_session: AsyncSession, tenants: tuple[_Tenant, _Tenant]
) -> None:
    """An aggregate is the easiest place to leak: one missing filter inflates every number."""
    alpha, _beta = tenants
    metrics = await DashboardRepository(db_session, alpha.agency.id).collect(
        as_of=datetime.now(UTC)
    )
    # Two tenants each hold exactly one transaction / alert / SAR draft, so every unscoped
    # aggregate would read 2 and every correctly scoped one reads 1.
    assert metrics.transaction_total == 1
    assert metrics.transaction_risk_bands == {RiskBand.HIGH.value: 1}
    assert metrics.alert_counts == {AlertStatus.OPEN.value: 1}
    assert metrics.sar_draft_count == 1


# --------------------------------------------------------------------------------------------------
# The batch job
# --------------------------------------------------------------------------------------------------


async def test_the_batch_selector_never_picks_up_another_tenants_rows(
    db_session: AsyncSession, tenants: tuple[_Tenant, _Tenant]
) -> None:
    alpha, beta = tenants
    unscored = Transaction(
        agency_id=beta.agency.id,
        external_id="beta-unscored",
        amount=Decimal("100.00"),
        currency="USD",
        occurred_at=datetime.now(UTC),
        origin_account="********3333",
        dest_account="********4444",
        channel="card",
        country="US",
        features={"dataset_source": "test-fixture"},
        feature_hash="d" * 64,
    )
    db_session.add(unscored)
    await db_session.commit()

    assert await select_uninvestigated(db_session, agency_id=alpha.agency.id) == []
    assert await select_uninvestigated(db_session, agency_id=beta.agency.id) == [unscored.id]


# --------------------------------------------------------------------------------------------------
# The shared workflow service (the one transition path the API and the bootstrap both use)
# --------------------------------------------------------------------------------------------------


def _workflow(session: AsyncSession, tenant: _Tenant) -> AlertWorkflowService:
    """Build the workflow service bound to one tenant, with its own correlated audit writer."""
    return AlertWorkflowService(
        session,
        agency_id=tenant.agency.id,
        audit=AuditLogRepository(session, agency_id=tenant.agency.id, request_id="isolation-test"),
    )


async def test_the_workflow_service_refuses_another_tenants_alert(
    db_session: AsyncSession, tenants: tuple[_Tenant, _Tenant]
) -> None:
    alpha, beta = tenants
    with pytest.raises(AppError) as refused:
        await _workflow(db_session, alpha).apply_action(
            AlertActionCommand(
                alert_id=beta.alert_id,
                actor_id=alpha.user.id,
                action=AlertActionType.ASSIGN,
                assignee_id=alpha.user.id,
            )
        )
    # Tenant-safe: "not found", never "forbidden" — a 403 would confirm the row exists.
    assert refused.value.code == "alert_not_found"


async def test_the_workflow_service_refuses_a_cross_tenant_assignee(
    db_session: AsyncSession, tenants: tuple[_Tenant, _Tenant]
) -> None:
    alpha, beta = tenants
    with pytest.raises(AppError) as refused:
        await _workflow(db_session, alpha).apply_action(
            AlertActionCommand(
                alert_id=alpha.alert_id,
                actor_id=alpha.user.id,
                action=AlertActionType.ASSIGN,
                assignee_id=beta.user.id,
            )
        )
    assert refused.value.code == "assignee_not_in_agency"


async def test_the_workflow_service_refuses_another_tenants_sar_review(
    db_session: AsyncSession, tenants: tuple[_Tenant, _Tenant]
) -> None:
    alpha, beta = tenants
    with pytest.raises(AppError) as refused:
        await _workflow(db_session, alpha).review_sar(
            SarReviewCommand(
                alert_id=beta.alert_id,
                actor_id=alpha.user.id,
                decision=SarReviewDecision.APPROVE,
            )
        )
    assert refused.value.code == "alert_not_found"


# --------------------------------------------------------------------------------------------------
# The HTTP surface
# --------------------------------------------------------------------------------------------------


async def test_the_api_returns_a_tenant_safe_404_for_another_tenants_rows(
    make_security_app: Callable[..., Any],
    accept: Callable[..., Callable[[], TokenVerifier]],
    aclient: Callable[[Any], httpx.AsyncClient],
    db_sessionmaker: async_sessionmaker[Any],
) -> None:
    async with db_sessionmaker() as session:
        alpha = await _populate(session, "httpalpha")
        beta = await _populate(session, "httpbeta")

    app = make_security_app()
    app.dependency_overrides[get_token_verifier] = accept(
        str(alpha.agency.id), role=UserRole.REVIEWER.value, user_id=str(alpha.user.id)
    )
    async with aclient(app) as client:
        own_alert = await client.get(f"/api/v1/alerts/{alpha.alert_id}", headers=AUTH)
        other_alert = await client.get(f"/api/v1/alerts/{beta.alert_id}", headers=AUTH)
        other_txn = await client.get(f"/api/v1/transactions/{beta.transaction_id}", headers=AUTH)
        listing = await client.get("/api/v1/alerts", headers=AUTH)

    assert own_alert.status_code == 200
    assert other_alert.status_code == 404  # no existence leak
    assert other_txn.status_code == 404
    ids = {row["alertId"] for row in listing.json()["alerts"]}
    assert ids == {str(alpha.alert_id)}
