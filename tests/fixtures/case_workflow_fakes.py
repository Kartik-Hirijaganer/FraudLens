"""Synthetic maker-checker fixtures shared by portable API and PostgreSQL race tests."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from starlette.requests import Request
from storage_fakes import _client, _demo_app

from fraudlens_backend.api.deps import AccessClaims, _dev_bypass_claims, authenticate
from fraudlens_backend.db.models import (
    Alert,
    AnalysisResult,
    AnalysisRun,
    SarDraft,
    Transaction,
    User,
)
from fraudlens_backend.db.models.enums import (
    AlertStatus,
    RunStatus,
    SarStatus,
    Severity,
    TransactionSource,
    UserRole,
)
from fraudlens_backend.portfolio_demo import load_portfolio_demo_config
from fraudlens_backend.services.person_identity import provision_person_link
from fraudlens_backend.settings import AppSettings
from fraudlens_core import RiskBand
from seed import seed

STORY = load_portfolio_demo_config()
AGENCY = STORY.agency.id
ALIAS_ACCOUNT_ID = uuid.uuid5(AGENCY, "test-only-maker-alias")


def account(key: str) -> uuid.UUID:
    """Resolve trusted configured persona without copying identities into source."""
    return ALIAS_ACCOUNT_ID if key == "maker-alias" else STORY.persona(key).seed_user_id


def headers(key: str) -> dict[str, str]:
    if key == "maker-alias":
        return {"X-Test-Actor-Alias": "true"}
    return {"X-FraudLens-Demo-Role": key}


async def seed_case(
    sm: async_sessionmaker[AsyncSession],
    *,
    risk_band: RiskBand = RiskBand.HIGH,
    probability: float = 0.91,
    sar: bool = True,
    failed: bool = False,
    running: bool = False,
) -> dict[str, uuid.UUID]:
    """Create immutable synthetic evidence with a case raised by the actual pipeline repository."""
    async with sm() as session:
        await seed(session, AppSettings(llm_mode="mock", rag_embedding_mode="offline"))
        tx = Transaction(
            agency_id=AGENCY,
            external_id=f"CASE-{uuid.uuid4()}",
            amount=Decimal("9500"),
            currency="USD",
            occurred_at=datetime.now(UTC),
            origin_account="****1111",
            dest_account="****2222",
            channel="wire",
            country="US",
            features={},
            feature_hash="synthetic",
            source=TransactionSource.PORTFOLIO_DEMO,
        )
        session.add(tx)
        await session.flush()
        run = AnalysisRun(
            agency_id=AGENCY,
            transaction_id=tx.id,
            status=RunStatus.RUNNING if running else RunStatus.COMPLETED,
            risk_band=risk_band,
            risk_score=0.91,
            model_version="v-test",
        )
        session.add(run)
        await session.flush()
        result = AnalysisResult(
            agency_id=AGENCY,
            run_id=run.id,
            fraud_probability=probability,
            risk_band=risk_band,
            combined_score=0.91,
            model_version="v-test",
            shap_values={},
            top_features=[],
            rule_hits=[],
        )
        session.add(result)
        alert = Alert(
            agency_id=AGENCY,
            transaction_id=tx.id,
            run_id=run.id,
            status=AlertStatus.OPEN,
            severity=Severity.HIGH,
            review_flags=[],
        )
        session.add(alert)
        await session.flush()
        ids = {"alert_id": alert.id, "run_id": run.id, "transaction_id": tx.id}
        if sar:
            draft = SarDraft(
                agency_id=AGENCY,
                alert_id=alert.id,
                run_id=run.id,
                version=1,
                model_id="mock",
                prompt_version="test",
                prompt_hash="test",
                status=SarStatus.FAILED if failed else SarStatus.DRAFT,
                content="" if failed else "Frozen original narrative. Final sentinel.",
                citations=[
                    {
                        "citation": "31 CFR 1020.320",
                        "title": "SAR",
                        "source": "FinCEN",
                        "snippet": "Synthetic grounding",
                    }
                ],
                structured={},
            )
            session.add(draft)
            await session.flush()
            ids["sar_id"] = draft.id
        await session.commit()
        return ids


@pytest.fixture
async def case_api(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
):
    ids = await seed_case(db_sessionmaker)
    async with db_sessionmaker() as session:
        session.add(
            User(
                id=ALIAS_ACCOUNT_ID,
                agency_id=AGENCY,
                email="alias@synthetic.test",
                display_name="Test-only alias",
                role=UserRole.ADMIN,
            )
        )
        await session.flush()
        await provision_person_link(
            session,
            agency_id=AGENCY,
            account_id=ALIAS_ACCOUNT_ID,
            person_id=STORY.persona("analyst").person_id,
        )
        await session.commit()
    app = _demo_app(make_settings, db_engine, db_sessionmaker)

    async def test_identity(request: Request):
        if request.headers.get("X-Test-Actor-Alias") == "true":
            return AccessClaims(agency_id=str(AGENCY), user_id=str(ALIAS_ACCOUNT_ID), role="admin")
        return _dev_bypass_claims(request, app.state.settings)

    app.dependency_overrides[authenticate] = test_identity
    async with _client(app) as client:
        yield client, ids, db_sessionmaker


async def detail(client, ids, persona="analyst"):
    response = await client.get(f"/api/v1/alerts/{ids['alert_id']}", headers=headers(persona))
    assert response.status_code == 200, response.text
    return response.json()


async def pickup(client, ids, persona="analyst"):
    d = await detail(client, ids, persona)
    response = await client.post(
        f"/api/v1/alerts/{ids['alert_id']}/actions",
        json={"action": "assign", "expectedCaseVersion": d["alert"]["caseVersion"]},
        headers=headers(persona),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def submit(client, ids, persona="analyst", recommendation="sar", key="submit"):
    d = await detail(client, ids, persona)
    body = {
        "expectedCaseVersion": d["alert"]["caseVersion"],
        "rationale": "Evidence reviewed; independent checking requested.",
        "recommendation": recommendation,
    }
    if recommendation == "sar":
        body["sarVersion"] = d["sarDraft"]["version"]
    response = await client.post(
        f"/api/v1/alerts/{ids['alert_id']}/submissions",
        json=body,
        headers={**headers(persona), "Idempotency-Key": key},
    )
    assert response.status_code == 200, response.text
    return response.json(), body


async def decide(client, ids, submission, decision="approve", persona="reviewer", key="decision"):
    d = await detail(client, ids, persona)
    body = {
        "expectedCaseVersion": d["alert"]["caseVersion"],
        "decision": decision,
        "reason": "Independent evidence review complete.",
    }
    if decision != "return":
        body["outcome"] = "confirmed_fraud" if decision == "approve" else "benign"
    return await client.post(
        f"/api/v1/alerts/{ids['alert_id']}/submissions/{submission['submissionId']}/decision",
        json=body,
        headers={**headers(persona), "Idempotency-Key": key},
    )


async def count(session, model):
    return (
        await session.execute(
            select(func.count()).select_from(model).where(model.agency_id == AGENCY)
        )
    ).scalar_one()
