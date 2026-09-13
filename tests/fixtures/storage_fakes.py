"""Alerts & review-workflow API tests (plan §5.4, §10.4, §16 Phase 9; endpoints 9-12). Covers the
acceptance criteria: list/detail tenant scoping (cross-tenant → 404), legal/illegal status
transitions (409), SAR-before-resolution compatibility, assign/comment/escalate/resolve/dismiss,
cross-tenant assignee → 403, PHI-masked
notes, resolve writing a training label, SAR approve/edit/reject (reason required) with a deferred
mock PDF, fail-closed acting-user enforcement, and that the pipeline alert-raise persists computed
review flags. The deferred-PDF test uses a file-backed SQLite engine so the background session
uses a separate connection (mirroring the investigations suite)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
from portfolio_demo_identity import DEMO_AGENCY_ID, DEMO_BYPASS_USER_ID
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)
from tenancy import new_user_id

from fraudlens_backend.db.models import (
    Agency,
    Alert,
    AlertOrigin,
    AlertStatus,
    AnalysisRun,
    RunStatus,
    SarDraft,
    SarStatus,
    Severity,
    Transaction,
    User,
    UserRole,
)
from fraudlens_backend.main import create_app
from fraudlens_backend.settings import AppSettings
from fraudlens_core import RiskBand

_REVIEWER_ID = new_user_id()
_OTHER_AGENCY_ID = uuid.UUID("66666666-6666-4666-8666-666666666666")
_OTHER_USER_ID = uuid.UUID("77777777-7777-4777-8777-777777777777")


def _client(app: object) -> httpx.AsyncClient:
    """An AsyncClient driving the ASGI app in-process (same loop as the DB)."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _demo_app(
    make_settings: Callable[..., AppSettings], engine: AsyncEngine, sm: Any, **kw: Any
) -> Any:
    """Build a dev-bypass app (tenant → demo agency, actor → demo analyst) wired to the test DB."""
    app = create_app(make_settings(environment="dev", auth_dev_bypass=True, **kw))
    app.state.db_engine = engine  # type: ignore[attr-defined]
    app.state.db_sessionmaker = sm  # type: ignore[attr-defined]
    return app


async def _ensure_identities(session: AsyncSession) -> None:
    """Insert the demo + other agencies and their users (idempotent within a test)."""
    if await session.get(Agency, DEMO_AGENCY_ID) is None:
        session.add(Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo-alerts"))
        session.add(Agency(id=_OTHER_AGENCY_ID, name="Other", slug="other-alerts"))
        session.add(
            User(
                id=DEMO_BYPASS_USER_ID,
                agency_id=DEMO_AGENCY_ID,
                email="bypass-actor@alerts.test",
                display_name="Demo Bypass Actor",
                role=UserRole.ADMIN,
            )
        )
        session.add(
            User(
                id=_REVIEWER_ID,
                agency_id=DEMO_AGENCY_ID,
                email="reviewer@alerts.test",
                display_name="Demo Reviewer",
                role=UserRole.REVIEWER,
            )
        )
        session.add(
            User(
                id=_OTHER_USER_ID,
                agency_id=_OTHER_AGENCY_ID,
                email="analyst@other.test",
                display_name="Other Analyst",
                role=UserRole.ANALYST,
            )
        )


async def _seed_alert(
    sm: async_sessionmaker[AsyncSession],
    *,
    agency_id: uuid.UUID = DEMO_AGENCY_ID,
    status: AlertStatus = AlertStatus.OPEN,
    origin: AlertOrigin = AlertOrigin.PIPELINE,
    with_sar: bool = True,
    sar_status: SarStatus = SarStatus.DRAFT,
    review_flags: list[dict[str, str]] | None = None,
    external_id: str | None = None,
    assigned_to: uuid.UUID | None = None,
) -> dict[str, uuid.UUID]:
    """Seed an alert (+ its transaction, run, and optional SAR draft); return the relevant ids."""
    async with sm() as session:
        await _ensure_identities(session)
        transaction = Transaction(
            agency_id=agency_id,
            external_id=external_id or f"T-{uuid.uuid4().hex[:8]}",
            amount=Decimal("9500.00"),
            currency="USD",
            occurred_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            origin_account="****1111",
            dest_account="****2222",
            channel="wire",
            country="US",
            features={},
            feature_hash="fh",
        )
        session.add(transaction)
        await session.flush()
        run = AnalysisRun(
            agency_id=agency_id,
            transaction_id=transaction.id,
            status=RunStatus.COMPLETED,
            risk_score=0.82,
            risk_band=RiskBand.HIGH,
            model_version="v-test",
        )
        session.add(run)
        await session.flush()
        sar_id: uuid.UUID | None = None
        if with_sar:
            draft = SarDraft(
                agency_id=agency_id,
                run_id=run.id,
                model_id="mock",
                prompt_version="sar-v1",
                prompt_hash="h",
                content="Original SAR narrative.",
                structured={},
                citations=[{"citation": "31 CFR 1010.314"}],
                status=sar_status,
            )
            session.add(draft)
            await session.flush()
            sar_id = draft.id
        alert = Alert(
            agency_id=agency_id,
            transaction_id=transaction.id,
            run_id=run.id,
            origin=origin,
            status=status,
            severity=Severity.HIGH,
            assigned_to=assigned_to,
            review_flags=review_flags or [],
        )
        session.add(alert)
        await session.flush()
        ids = {
            "alert_id": alert.id,
            "run_id": run.id,
            "transaction_id": transaction.id,
        }
        if sar_id is not None:
            ids["sar_id"] = sar_id
        await session.commit()
        return ids


# --------------------------------------------------------------------------------------------------
# List + detail
# --------------------------------------------------------------------------------------------------
