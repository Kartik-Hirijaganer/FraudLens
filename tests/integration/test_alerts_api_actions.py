"""Alert listing, detail, action, permission, and label integration tests."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from portfolio_demo_identity import DEMO_AGENCY_ID, DEMO_BYPASS_USER_ID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)
from storage_fakes import (
    _OTHER_AGENCY_ID,
    _OTHER_USER_ID,
    _REVIEWER_ID,
    _client,
    _demo_app,
    _seed_alert,
)

from fraudlens_backend.api.deps import get_tenant
from fraudlens_backend.db.models import (
    AgentExecution,
    AlertAction,
    AlertOrigin,
    AlertStatus,
    AnalysisResult,
    AnalysisRun,
    AuditLog,
    RagRetrieval,
    Transaction,
    TransactionSource,
)
from fraudlens_backend.db.models.enums import AgentExecutionStatus, AgentRole
from fraudlens_backend.models.common import TenantContext
from fraudlens_backend.settings import AppSettings
from fraudlens_core import RiskBand


async def test_list_alerts_scoped_and_status_filter(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_alert(db_sessionmaker, status=AlertStatus.OPEN, external_id="A1")
    await _seed_alert(db_sessionmaker, status=AlertStatus.RESOLVED, external_id="A2")
    await _seed_alert(db_sessionmaker, status=AlertStatus.PENDING_REVIEW, external_id="A4")
    await _seed_alert(db_sessionmaker, status=AlertStatus.ESCALATED, external_id="A5")
    await _seed_alert(db_sessionmaker, agency_id=_OTHER_AGENCY_ID, external_id="A3")  # cross-tenant
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        all_resp = await client.get("/api/v1/alerts")
        open_resp = await client.get("/api/v1/alerts", params={"status": "open"})
        pending_resp = await client.get("/api/v1/alerts", params={"status": "pending_review"})
        escalated_resp = await client.get("/api/v1/alerts", params={"status": "escalated"})
    assert all_resp.status_code == 200
    assert len(all_resp.json()["alerts"]) == 4  # only the demo agency's alerts
    assert [a["status"] for a in open_resp.json()["alerts"]] == ["open"]
    assert [a["status"] for a in pending_resp.json()["alerts"]] == ["pending_review"]
    assert [a["status"] for a in escalated_resp.json()["alerts"]] == ["escalated"]
    assert all_resp.json()["alerts"][0]["amount"] == "9500.00"
    assert all_resp.json()["alerts"][0]["currency"] == "USD"
    assert all(alert["origin"] == "pipeline" for alert in all_resp.json()["alerts"])


async def test_get_alert_detail_surfaces_sar_and_flags(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    flags = [{"flag": "critical_risk_band", "reason": "Risk band is critical."}]
    ids = await _seed_alert(
        db_sessionmaker,
        review_flags=flags,
        origin=AlertOrigin.SEED,
        assigned_to=_REVIEWER_ID,
    )
    async with db_sessionmaker() as session:
        run = await session.get(AnalysisRun, ids["run_id"])
        transaction = await session.get(Transaction, ids["transaction_id"])
        transaction.source = TransactionSource.PORTFOLIO_DEMO
        run.workflow_mode = "multi_agent"
        run.graph_version = "agents-v1"
        session.add(
            AnalysisResult(
                agency_id=DEMO_AGENCY_ID,
                run_id=ids["run_id"],
                fraud_probability=0.91,
                shap_values={"amount_log": 0.4},
                top_features=[{"feature": "amount_log", "value": 9.2, "shapValue": 0.4}],
                rule_hits=[],
                combined_score=0.82,
                risk_band=RiskBand.HIGH,
                model_version="v-test",
            )
        )
        session.add(
            RagRetrieval(
                agency_id=DEMO_AGENCY_ID,
                run_id=ids["run_id"],
                query="synthetic case",
                top_k=1,
                chunks=[],
                rag_version="rag-test",
            )
        )
        session.add(
            AgentExecution(
                agency_id=DEMO_AGENCY_ID,
                run_id=ids["run_id"],
                agent=AgentRole.EVIDENCE_INVESTIGATOR,
                attempt=1,
                model_id="mock",
                prompt_version="v1",
                prompt_hash="p" * 64,
                input_hash="i" * 64,
                result_hash="r" * 64,
                status=AgentExecutionStatus.COMPLETED,
                latency_ms=0,
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                cost_usd=Decimal("0"),
                model_call_count=2,
                result={"outcome": "completed"},
                tool_calls=[],
            )
        )
        await session.commit()
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.get(f"/api/v1/alerts/{ids['alert_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert (
        body["alert"]["reviewFlags"] == []
    )  # Persisted HIGH/.91 evidence overrides historical flags.
    assert body["alert"]["origin"] == "seed"
    assert body["alert"]["amount"] == "9500.00"
    assert body["alert"]["currency"] == "USD"
    assert body["alert"]["assignedTo"] == str(_REVIEWER_ID)
    assert body["alert"]["assignedToName"] == "Demo Reviewer"
    assert body["sarDraft"]["citations"][0]["citation"] == "31 CFR 1010.314"
    assert body["sarDraft"]["modelInput"]["caseAlias"] == "case-under-review"
    assert body["sarDraft"]["modelInput"]["subjectAlias"] == "subject-account"
    assert body["sarDraft"]["modelInput"]["fraudProbability"] == 0.91
    assert str(ids["transaction_id"]) not in str(body["sarDraft"]["modelInput"])
    assert body["workflowMode"] == "multi_agent"
    assert body["graphVersion"] == "agents-v1"
    assert body["revisionCount"] == 0
    assert body["sarContent"] == "Original SAR narrative."
    assert body["agentExecutions"][0]["agent"] == "evidence_investigator"
    assert body["agentExecutions"][0]["modelCallCount"] == 2
    assert body["actions"] == []


async def test_get_alert_cross_tenant_returns_404(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker, agency_id=_OTHER_AGENCY_ID)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.get(f"/api/v1/alerts/{ids['alert_id']}")
    assert resp.status_code == 404
    assert resp.json()["code"] == "alert_not_found"


async def test_pickup_and_cross_tenant_assignment_are_checked(
    make_settings, db_engine, db_sessionmaker
):
    ids = await _seed_alert(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        forbidden = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions",
            json={"action": "assign", "expectedCaseVersion": 1, "assigneeId": str(_OTHER_USER_ID)},
        )
        assert forbidden.status_code == 403
        response = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions",
            json={"action": "assign", "expectedCaseVersion": 1},
        )
        assert response.status_code == 200
        assert response.json()["assignedTo"] == str(DEMO_BYPASS_USER_ID)
        assert response.json()["status"] == "in_review"
    async with db_sessionmaker() as session:
        action = (
            await session.execute(
                select(AlertAction).where(
                    AlertAction.agency_id == DEMO_AGENCY_ID, AlertAction.alert_id == ids["alert_id"]
                )
            )
        ).scalar_one()
        assert action.actor_person_id is not None
        assert action.actor_id == DEMO_BYPASS_USER_ID
        audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.agency_id == DEMO_AGENCY_ID, AuditLog.action == "case.assign"
                )
            )
        ).scalar_one()
        assert audit.resource_id == str(ids["alert_id"])


async def test_comment_note_is_phi_masked(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions",
            json={
                "action": "comment",
                "expectedCaseVersion": 1,
                "note": "reach me at evil@example.com about this",
            },
        )
    assert resp.status_code == 200
    async with db_sessionmaker() as session:
        action = (
            await session.execute(
                select(AlertAction).where(AlertAction.alert_id == ids["alert_id"])
            )
        ).scalar_one()
        audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.agency_id == DEMO_AGENCY_ID,
                    AuditLog.action == "phi_mask",
                    AuditLog.resource_id == str(ids["alert_id"]),
                )
            )
        ).scalar_one()
    assert "evil@example.com" not in (action.note or "")  # PHI-shaped span masked
    assert "[REDACTED_EMAIL]" in (action.note or "")
    assert audit.meta == {
        "source": "case.action",
        "maskedCount": "1",
        "categories": "email:1",
    }


async def test_acting_user_required_fails_closed(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    # Simulate a verified token that carries no subject (no acting user).
    app.dependency_overrides[get_tenant] = lambda: TenantContext(
        agency_id=str(DEMO_AGENCY_ID), user_id=None
    )
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions",
            json={"action": "comment", "note": "Comment", "expectedCaseVersion": 1},
        )
    assert resp.status_code == 401
    assert resp.json()["code"] == "acting_user_required"
