"""Alert listing, detail, action, permission, and label integration tests."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest
from portfolio_demo_identity import DEMO_AGENCY_ID, DEMO_BYPASS_USER_ID, demo_user_id
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
    SarStatus,
    TrainingLabel,
    Transaction,
    TransactionSource,
    UserRole,
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
    assert body["alert"]["reviewFlags"][0]["flag"] == "critical_risk_band"
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


async def test_assign_moves_to_in_review_and_audits(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions",
            json={"action": "assign", "assigneeId": str(_REVIEWER_ID)},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "in_review"
    assert body["assignedTo"] == str(_REVIEWER_ID)
    assert body["assignedToName"] == "Demo Reviewer"
    async with db_sessionmaker() as session:
        action = (
            await session.execute(
                select(AlertAction).where(AlertAction.alert_id == ids["alert_id"])
            )
        ).scalar_one()
        assert action.action.value == "assign"
        assert action.actor_id == DEMO_BYPASS_USER_ID  # the dev-bypass acting user
        assert action.to_status == "in_review"
        audit = (
            await session.execute(select(AuditLog).where(AuditLog.action == "alert.assign"))
        ).scalar_one()
        assert audit.resource_id == str(ids["alert_id"])
        assert audit.actor_id == DEMO_BYPASS_USER_ID
        assert audit.meta["assigneeId"] == str(_REVIEWER_ID)


async def test_assign_cross_tenant_assignee_returns_403(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions",
            json={"action": "assign", "assigneeId": str(_OTHER_USER_ID)},
        )
    assert resp.status_code == 403
    assert resp.json()["code"] == "assignee_not_in_agency"


async def test_auditor_cannot_mutate_alerts(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker, auth_dev_bypass_role="auditor")
    async with _client(app) as client:
        detail = await client.get(f"/api/v1/alerts/{ids['alert_id']}")
        resp = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions", json={"action": "comment"}
        )
    assert detail.status_code == 200
    assert resp.status_code == 403
    assert resp.json()["code"] == "role_permission_required"


async def test_resolve_writes_training_label(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker, sar_status=SarStatus.APPROVED)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions",
            json={"action": "resolve", "label": "confirmed_fraud"},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"
    async with db_sessionmaker() as session:
        label = (
            await session.execute(
                select(TrainingLabel).where(TrainingLabel.run_id == ids["run_id"])
            )
        ).scalar_one()
        assert label.label.value == "confirmed_fraud"
        assert label.source.value == "analyst_review"
        assert label.created_by == DEMO_BYPASS_USER_ID
        assert label.transaction_id == ids["transaction_id"]
        assert label.matured_at is not None  # a future maturity is stamped for the retrain job


async def test_resolve_requires_an_existing_sar_decision(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker, sar_status=SarStatus.DRAFT)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions",
            json={"action": "resolve", "label": "confirmed_fraud"},
        )
    assert resp.status_code == 409
    assert resp.json()["code"] == "sar_decision_required"


@pytest.mark.parametrize(
    ("sar_status", "label"),
    [
        (SarStatus.APPROVED, "false_positive"),
        (SarStatus.REJECTED, "confirmed_fraud"),
    ],
)
async def test_resolve_rejects_an_outcome_that_conflicts_with_the_sar_decision(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    sar_status: SarStatus,
    label: str,
) -> None:
    ids = await _seed_alert(db_sessionmaker, sar_status=sar_status)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions",
            json={"action": "resolve", "label": label},
        )
    assert resp.status_code == 409
    assert resp.json()["code"] == "resolution_label_mismatch"


async def test_dismiss_rejects_an_approved_sar(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker, sar_status=SarStatus.APPROVED)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions",
            json={"action": "dismiss"},
        )
    assert resp.status_code == 409
    assert resp.json()["code"] == "resolution_label_mismatch"


async def test_resolve_without_a_sar_allows_a_manual_outcome(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker, with_sar=False)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions",
            json={"action": "resolve", "label": "confirmed_fraud"},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"


async def test_resolve_without_label_is_422(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions", json={"action": "resolve"}
        )
    assert resp.status_code == 422


@pytest.mark.parametrize(
    ("action", "expected", "sar_status"),
    [
        ("escalate", "escalated", SarStatus.DRAFT),
        ("dismiss", "dismissed", SarStatus.REJECTED),
    ],
)
async def test_escalate_and_dismiss_transitions(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    action: str,
    expected: str,
    sar_status: SarStatus,
) -> None:
    ids = await _seed_alert(db_sessionmaker, sar_status=sar_status)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions", json={"action": action}
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == expected


async def test_illegal_transition_on_terminal_alert_is_409(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker, status=AlertStatus.RESOLVED)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions", json={"action": "comment", "note": "hi"}
        )
    assert resp.status_code == 409
    assert resp.json()["code"] == "invalid_alert_transition"


async def test_analyst_cannot_finalize_alert_or_review_sar(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker)
    dismiss_ids = await _seed_alert(db_sessionmaker, sar_status=SarStatus.REJECTED)
    app = _demo_app(make_settings, db_engine, db_sessionmaker, auth_dev_bypass_role="analyst")
    async with _client(app) as client:
        comment = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions", json={"action": "comment"}
        )
        dismiss = await client.post(
            f"/api/v1/alerts/{dismiss_ids['alert_id']}/actions", json={"action": "dismiss"}
        )
        resolve = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions",
            json={"action": "resolve", "label": "confirmed_fraud"},
        )
        review = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/sar/review", json={"decision": "approve"}
        )
    assert comment.status_code == 200
    assert dismiss.status_code == 200
    assert dismiss.json()["status"] == "dismissed"
    assert resolve.status_code == 403
    assert review.status_code == 403
    assert resolve.json()["code"] == "role_permission_required"
    assert review.json()["code"] == "role_permission_required"
    async with db_sessionmaker() as session:
        label = (
            await session.execute(
                select(TrainingLabel).where(TrainingLabel.run_id == dismiss_ids["run_id"])
            )
        ).scalar_one()
    assert label.label.value == "false_positive"
    assert label.source.value == "analyst_dismiss"
    # This app runs the bypass as `analyst`, so the actor is the ANALYST persona, not the default.
    assert label.created_by == demo_user_id(UserRole.ANALYST)


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
            json={"action": "comment", "note": "reach me at evil@example.com about this"},
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
        "source": "alert.action_note",
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
            f"/api/v1/alerts/{ids['alert_id']}/actions", json={"action": "comment"}
        )
    assert resp.status_code == 401
    assert resp.json()["code"] == "acting_user_required"
