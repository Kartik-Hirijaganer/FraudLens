"""PostgreSQL proofs of row-lock serialization, immutable identities and migration round trips."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator

import pytest
from alembic import command
from alembic.config import Config
from case_workflow_fakes import AGENCY, STORY, account, seed_case
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fraudlens_backend.db.models import (
    AccountPerson,
    Alert,
    CaseDecision,
    CaseSubmission,
    SarDraft,
    TrainingLabel,
)
from fraudlens_backend.db.repositories import AuditLogRepository
from fraudlens_backend.models.case_workflow import (
    DecisionRequest,
    SubmissionRequest,
    WorkingSarRequest,
)
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.services.alert_workflow import AlertWorkflowService
from fraudlens_backend.services.sar_regeneration import regenerate_sar_for_run
from fraudlens_backend.settings import AppSettings

pytestmark = pytest.mark.postgres


@pytest.fixture
async def postgres_cases() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("POSTGRES_TEST_DATABASE_URL")
    if not url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is not configured")
    engine = create_async_engine(url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def workflow(session: AsyncSession) -> AlertWorkflowService:
    return AlertWorkflowService(
        session,
        agency_id=AGENCY,
        audit=AuditLogRepository(session, agency_id=AGENCY, request_id="postgres-case-race"),
    )


async def invoke(sm, operation):
    async with sm() as session:
        try:
            value = await operation(session, workflow(session))
            await session.commit()
            return value
        except AppError as exc:
            await session.rollback()
            return exc.code


async def prepare(sm, ids):
    async with sm() as session:
        await workflow(session).act(
            alert_id=ids["alert_id"],
            actor_id=account("analyst"),
            action="assign",
            expected_case_version=1,
        )
        await session.commit()


async def submit(sm, ids):
    async with sm() as session:
        result = await workflow(session).submit(
            alert_id=ids["alert_id"],
            actor_id=account("analyst"),
            payload=SubmissionRequest(
                expected_case_version=2,
                rationale="Independent review requested",
                recommendation="sar",
                sar_version=1,
            ),
            idempotency_key="submit",
        )
        await session.commit()
        return result


async def test_concurrent_pickup_has_one_owner_and_action(postgres_cases):
    sm = postgres_cases
    ids = await seed_case(sm)

    async def pickup(session, service, actor):
        await service.act(
            alert_id=ids["alert_id"],
            actor_id=account(actor),
            action="assign",
            expected_case_version=1,
        )
        return "picked"

    results = await asyncio.gather(
        invoke(sm, lambda s, w: pickup(s, w, "analyst")),
        invoke(sm, lambda s, w: pickup(s, w, "reviewer")),
    )
    assert sorted(results) == ["case_version_conflict", "picked"]
    async with sm() as session:
        alert = await session.get(Alert, ids["alert_id"])
        assert alert.case_version == 2 and alert.assigned_to in {
            account("analyst"),
            account("reviewer"),
        }


async def test_edit_versus_submit_serializes_without_changing_frozen_content(postgres_cases):
    sm = postgres_cases
    ids = await seed_case(sm)
    await prepare(sm, ids)

    async def edit(session, service):
        await service.save_sar(
            alert_id=ids["alert_id"],
            actor_id=account("analyst"),
            payload=WorkingSarRequest(
                expected_case_version=2,
                expected_sar_version=1,
                content="Concurrent revised narrative",
            ),
        )
        return "edited"

    async def submit_op(session, service):
        await service.submit(
            alert_id=ids["alert_id"],
            actor_id=account("analyst"),
            payload=SubmissionRequest(
                expected_case_version=2, rationale="Review", recommendation="sar", sar_version=1
            ),
            idempotency_key="submit",
        )
        return "submitted"

    results = await asyncio.gather(invoke(sm, edit), invoke(sm, submit_op))
    assert results.count("case_version_conflict") == 1
    async with sm() as session:
        alert = await session.get(Alert, ids["alert_id"])
        submissions = (
            (
                await session.execute(
                    select(CaseSubmission).where(
                        CaseSubmission.agency_id == AGENCY, CaseSubmission.alert_id == alert.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert alert.case_version == 3
        if submissions:
            draft = await session.get(SarDraft, submissions[0].sar_draft_id)
            assert draft.content == "Frozen original narrative. Final sentinel."
        else:
            assert "edited" in results


async def test_regeneration_versus_submit_uses_the_same_lock(postgres_cases):
    sm = postgres_cases
    ids = await seed_case(sm)
    await prepare(sm, ids)

    async def regenerate(session, service):
        await regenerate_sar_for_run(
            session=session,
            agency_id=AGENCY,
            run_id=ids["run_id"],
            settings=AppSettings(llm_mode="mock", rag_embedding_mode="offline"),
            created_by=account("analyst"),
            expected_case_version=2,
        )
        return "regenerated"

    async def submit_op(session, service):
        await service.submit(
            alert_id=ids["alert_id"],
            actor_id=account("analyst"),
            payload=SubmissionRequest(
                expected_case_version=2, rationale="Review", recommendation="sar", sar_version=1
            ),
            idempotency_key="submit",
        )
        return "submitted"

    results = await asyncio.gather(invoke(sm, regenerate), invoke(sm, submit_op))
    assert results.count("case_version_conflict") == 1
    async with sm() as session:
        assert (await session.get(Alert, ids["alert_id"])).case_version == 3


async def test_competing_decisions_commit_one_label_reason_and_decision(postgres_cases):
    sm = postgres_cases
    ids = await seed_case(sm)
    await prepare(sm, ids)
    submitted = await submit(sm, ids)

    async def decide(session, service, actor, decision):
        await service.decide(
            alert_id=ids["alert_id"],
            actor_id=account(actor),
            submission_id=uuid.UUID(submitted.submission_id),
            payload=DecisionRequest(
                expected_case_version=3,
                decision=decision,
                reason=f"Independent {decision} reason",
                outcome="confirmed_fraud" if decision == "approve" else "benign",
            ),
            idempotency_key=decision,
        )
        return decision

    results = await asyncio.gather(
        invoke(sm, lambda s, w: decide(s, w, "reviewer", "approve")),
        invoke(sm, lambda s, w: decide(s, w, "admin", "decline")),
    )
    assert results.count("case_version_conflict") == 1
    async with sm() as session:
        decisions = (
            (
                await session.execute(
                    select(CaseDecision).where(
                        CaseDecision.agency_id == AGENCY,
                        CaseDecision.submission_id == uuid.UUID(submitted.submission_id),
                    )
                )
            )
            .scalars()
            .all()
        )
        labels = (
            (
                await session.execute(
                    select(TrainingLabel).where(
                        TrainingLabel.agency_id == AGENCY, TrainingLabel.run_id == ids["run_id"]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(decisions) == len(labels) == 1
        assert (await session.get(Alert, ids["alert_id"])).status.value == "resolved"
        assert (await session.get(SarDraft, ids["sar_id"])).status.value == (
            "approved" if decisions[0].decision == "approve" else "rejected"
        )


async def test_simultaneous_identical_submission_replays_one_round(postgres_cases):
    sm = postgres_cases
    ids = await seed_case(sm)
    await prepare(sm, ids)

    async def submit_op(session, service):
        return await service.submit(
            alert_id=ids["alert_id"],
            actor_id=account("analyst"),
            payload=SubmissionRequest(
                expected_case_version=2, rationale="Review", recommendation="sar", sar_version=1
            ),
            idempotency_key="identical",
        )

    results = await asyncio.gather(invoke(sm, submit_op), invoke(sm, submit_op))
    assert results[0] == results[1]
    async with sm() as session:
        assert (
            await session.execute(
                select(func.count())
                .select_from(CaseSubmission)
                .where(
                    CaseSubmission.agency_id == AGENCY, CaseSubmission.alert_id == ids["alert_id"]
                )
            )
        ).scalar_one() == 1


async def test_database_rejects_identity_remap_and_frozen_submission_changes(postgres_cases):
    sm = postgres_cases
    ids = await seed_case(sm)
    await prepare(sm, ids)
    submitted = await submit(sm, ids)
    async with sm() as session:
        link = (
            await session.execute(
                select(AccountPerson).where(
                    AccountPerson.agency_id == AGENCY,
                    AccountPerson.account_id == account("analyst"),
                )
            )
        ).scalar_one()
        link.person_id = STORY.persona("reviewer").person_id
        with pytest.raises(DBAPIError):
            await session.flush()
        await session.rollback()
        row = await session.get(CaseSubmission, uuid.UUID(submitted.submission_id))
        row.contributor_person_ids = []
        with pytest.raises(DBAPIError):
            await session.flush()
        await session.rollback()


async def test_maker_dismissal_persists_postgres_label_source(postgres_cases):
    sm = postgres_cases
    ids = await seed_case(sm)
    await prepare(sm, ids)
    async with sm() as session:
        await workflow(session).act(
            alert_id=ids["alert_id"],
            actor_id=account("analyst"),
            action="dismiss",
            expected_case_version=2,
            note="Independent evidence shows no reportable concern.",
        )
        await session.commit()
    async with sm() as session:
        label = (
            await session.execute(
                select(TrainingLabel).where(
                    TrainingLabel.agency_id == AGENCY,
                    TrainingLabel.run_id == ids["run_id"],
                )
            )
        ).scalar_one()
        assert label.source.value == "analyst_dismiss" and label.label.value == "false_positive"
        assert (await session.get(Alert, ids["alert_id"])).status.value == "dismissed"
        assert (await session.get(SarDraft, ids["sar_id"])).status.value == "withdrawn"


async def _migration_database(url, name, *, create):
    admin = create_async_engine(
        make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        async with admin.connect() as conn:
            sql = f'CREATE DATABASE "{name}"' if create else f'DROP DATABASE "{name}" WITH (FORCE)'
            await conn.execute(text(sql))
    finally:
        await admin.dispose()


async def _historical_rows(url, *, create=False, upgraded=False):
    engine = create_async_engine(url)
    agency, user, tx, run, alert, draft, action = [str(uuid.uuid4()) for _ in range(7)]
    params = {
        "agency": agency,
        "user": user,
        "tx": tx,
        "run": run,
        "alert": alert,
        "draft": draft,
        "action": action,
    }
    try:
        async with engine.begin() as conn:
            if create:
                statements = [
                    (
                        "INSERT INTO agencies (id, name, slug) VALUES (:agency, 'History', "
                        "'history')"
                    ),
                    (
                        "INSERT INTO users (id, agency_id, email, display_name, role) VALUES "
                        "(:user, :agency, 'historical@synthetic.test', 'Historical reviewer', "
                        "'reviewer')"
                    ),
                    (
                        "INSERT INTO transactions (id, agency_id, external_id, amount, currency, "
                        "occurred_at, origin_account, dest_account, channel, country, features, "
                        "feature_hash) VALUES (:tx, :agency, 'historical', 1, 'USD', now(), "
                        "'****1111', '****2222', 'wire', 'US', '{}', 'history')"
                    ),
                    (
                        "INSERT INTO analysis_runs (id, agency_id, transaction_id, status) VALUES "
                        "(:run, :agency, :tx, 'completed')"
                    ),
                    (
                        "INSERT INTO alerts (id, agency_id, transaction_id, run_id, status, "
                        "severity, review_flags) VALUES (:alert, :agency, :tx, :run, 'resolved', "
                        "'high', '[]')"
                    ),
                    (
                        "INSERT INTO sar_drafts (id, agency_id, run_id, alert_id, version, "
                        "model_id, prompt_version, prompt_hash, content, structured, citations, "
                        "status, token_usage, cost_usd, quality) VALUES (:draft, :agency, :run, "
                        ":alert, 1, 'mock', 'history', 'history', 'Historical narrative.', '{}', "
                        "'[]', 'approved', '{}', 0, '{}')"
                    ),
                    (
                        "INSERT INTO alert_actions (id, agency_id, alert_id, actor_id, action, "
                        "note) VALUES (:action, :agency, :alert, :user, 'resolve', 'Historical "
                        "masked reason.')"
                    ),
                ]
                for statement in statements:
                    await conn.execute(text(statement), params)
            assert (
                await conn.execute(text("SELECT note FROM alert_actions"))
            ).scalar_one() == "Historical masked reason."
            assert (
                await conn.execute(text("SELECT content FROM sar_drafts"))
            ).scalar_one() == "Historical narrative."
            if upgraded:
                assert (
                    await conn.execute(text("SELECT actor_person_id FROM alert_actions"))
                ).scalar_one() is None
                assert (
                    await conn.execute(text("SELECT count(*) FROM account_persons"))
                ).scalar_one() == 0
                assert (
                    await conn.execute(text("SELECT count(*) FROM case_submissions"))
                ).scalar_one() == 0
    finally:
        await engine.dispose()


def test_postgres_migration_round_trip_preserves_historical_records():
    url = os.environ.get("POSTGRES_TEST_DATABASE_URL")
    if not url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is not configured")
    if make_url(url).host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("Migration round trips require an explicitly local disposable PostgreSQL host")
    name = "maker_checker_migration_" + uuid.uuid4().hex
    migration_url = make_url(url).set(database=name).render_as_string(hide_password=False)
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", migration_url)
    asyncio.run(_migration_database(url, name, create=True))
    try:
        command.upgrade(cfg, "0015_account_identity_keys")
        asyncio.run(_historical_rows(migration_url, create=True))
        command.upgrade(cfg, "head")
        asyncio.run(_historical_rows(migration_url, upgraded=True))
        command.downgrade(cfg, "0015_account_identity_keys")
        asyncio.run(_historical_rows(migration_url))
        command.upgrade(cfg, "head")
        asyncio.run(_historical_rows(migration_url, upgraded=True))
    finally:
        asyncio.run(_migration_database(url, name, create=False))
