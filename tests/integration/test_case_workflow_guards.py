"""Failure-atomicity, immutable identity, stable evidence and delayed-writer acceptance proofs."""

from __future__ import annotations

import uuid

import pytest
from case_workflow_fakes import (
    AGENCY,
    STORY,
    account,
    count,
    decide,
    detail,
    headers,
    pickup,
    seed_case,
    submit,
)

from fraudlens_backend.db.models import (
    Agency,
    CaseContributor,
    CaseDecision,
    CaseSubmission,
    TrainingLabel,
    User,
)
from fraudlens_backend.db.models.enums import Severity, UserRole
from fraudlens_backend.db.repositories import AnalysisRunRepository, SarDraftRepository
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.services.person_identity import provision_person_link, resolve_actor
from fraudlens_core import RiskBand
from fraudlens_ml.sar import SarDraftResult, SarDraftStatus


async def test_aliases_map_once_and_cross_tenant_links_or_remaps_are_refused(case_api):
    _, _, sm = case_api
    async with sm() as session:
        analyst = await resolve_actor(session, AGENCY, account("analyst"))
        alias = await resolve_actor(session, AGENCY, account("maker-alias"))
        assert analyst.person_id == alias.person_id and analyst.account_id != alias.account_id
        with pytest.raises(AppError, match="person_link_immutable"):
            await provision_person_link(
                session,
                agency_id=AGENCY,
                account_id=analyst.account_id,
                person_id=STORY.persona("reviewer").person_id,
            )
        other = Agency(name="Other synthetic tenant", slug=f"other-{uuid.uuid4().hex}")
        session.add(other)
        await session.flush()
        user = User(
            agency_id=other.id,
            email=f"test-{uuid.uuid4().hex}@tenant.test",
            display_name="Other",
            role=UserRole.ADMIN,
        )
        session.add(user)
        await session.flush()
        with pytest.raises(AppError, match="person_link_immutable"):
            await provision_person_link(
                session, agency_id=other.id, account_id=user.id, person_id=analyst.person_id
            )
        with pytest.raises(AppError, match="user_not_provisioned"):
            await provision_person_link(
                session, agency_id=other.id, account_id=analyst.account_id, person_id=uuid.uuid4()
            )
        assert (
            await resolve_actor(session, AGENCY, analyst.account_id)
        ).person_id == analyst.person_id


async def test_unassigned_and_unstable_submissions_make_no_partial_writes(case_api):
    client, ids, sm = case_api
    endpoint = f"/api/v1/alerts/{ids['alert_id']}/submissions"
    payload = {
        "expectedCaseVersion": 1,
        "recommendation": "sar",
        "sarVersion": 1,
        "rationale": "Review.",
    }
    assert (
        await client.post(
            endpoint, json=payload, headers={**headers("analyst"), "Idempotency-Key": "unassigned"}
        )
    ).status_code == 403
    unstable = await seed_case(sm, running=True)
    await pickup(client, unstable)
    response = await client.post(
        f"/api/v1/alerts/{unstable['alert_id']}/submissions",
        json={**payload, "expectedCaseVersion": 2},
        headers={**headers("analyst"), "Idempotency-Key": "writing"},
    )
    assert response.status_code == 403
    async with sm() as session:
        assert await count(session, CaseSubmission) == await count(session, CaseContributor) == 0
        assert await count(session, TrainingLabel) == await count(session, CaseDecision) == 0


async def test_stale_narrative_and_blank_idempotency_keys_leave_artifacts_unchanged(case_api):
    client, ids, sm = case_api
    await pickup(client, ids)
    before = await detail(client, ids)
    for sar_version, case_version in [(None, 2), (2, 2), (1, 1)]:
        response = await client.patch(
            f"/api/v1/alerts/{ids['alert_id']}/sar",
            json={
                "expectedCaseVersion": case_version,
                "expectedSarVersion": sar_version,
                "content": "Forbidden stale edit.",
            },
            headers=headers("analyst"),
        )
        assert response.status_code == 409
    response = await client.post(
        f"/api/v1/alerts/{ids['alert_id']}/submissions",
        json={
            "expectedCaseVersion": 2,
            "recommendation": "sar",
            "sarVersion": 1,
            "rationale": "Reviewed.",
        },
        headers={**headers("analyst"), "Idempotency-Key": " "},
    )
    assert response.status_code == 422
    after = await detail(client, ids)
    assert before["sarDraft"] == after["sarDraft"] and before["actions"] == after["actions"]
    async with sm() as session:
        assert await count(session, CaseContributor) == await count(session, CaseSubmission) == 0


async def test_same_narrative_is_a_noop_but_alias_changes_preserve_original_owner(case_api):
    client, ids, _ = case_api
    await pickup(client, ids)
    d = await detail(client, ids)
    response = await client.patch(
        f"/api/v1/alerts/{ids['alert_id']}/sar",
        json={
            "expectedCaseVersion": 2,
            "expectedSarVersion": 1,
            "content": d["sarDraft"]["content"],
        },
        headers=headers("maker-alias"),
    )
    assert response.status_code == 200
    assert (await detail(client, ids))["alert"]["caseVersion"] == 2
    response = await client.patch(
        f"/api/v1/alerts/{ids['alert_id']}/sar",
        json={
            "expectedCaseVersion": 2,
            "expectedSarVersion": 1,
            "content": "Alias authored revision.",
        },
        headers=headers("maker-alias"),
    )
    assert response.status_code == 200
    submitted, _ = await submit(client, ids, persona="maker-alias")
    d = await detail(client, ids)
    assert d["submissions"][0]["makerAccountId"] == str(account("analyst"))
    assert d["submissions"][0]["submittedByAccountId"] == str(account("maker-alias"))
    assert (await decide(client, ids, submitted, decision="return")).status_code == 200
    d = await detail(client, ids)
    assert d["alert"]["assignedTo"] == str(account("analyst"))
    assert d["sarDraft"]["version"] == 3


@pytest.mark.parametrize("state", ["owned", "submitted", "closed"])
async def test_late_machine_writers_preserve_human_state_and_narrative(case_api, state):
    client, ids, sm = case_api
    await pickup(client, ids)
    if state != "owned":
        submission, _ = await submit(client, ids)
        if state == "closed":
            assert (await decide(client, ids, submission, "decline")).status_code == 200
    before = await detail(client, ids)
    async with sm() as session:
        repository = AnalysisRunRepository(session, AGENCY)
        await repository.update_alert_review_flags(
            run_id=ids["run_id"],
            review_flags=[{"flag": "sar_unavailable", "reason": "Late failure"}],
        )
        await repository.raise_alert(
            run_id=ids["run_id"],
            transaction_id=ids["transaction_id"],
            severity=Severity.CRITICAL,
            review_flags=[],
        )
        # A genuine machine result arriving late must reuse the human artifact.

        late = SarDraftResult(
            status=SarDraftStatus.FAILED,
            content="",
            model_id="mock",
            prompt_version="test",
            prompt_hash="late",
        )
        draft = await SarDraftRepository(session, AGENCY).create_from_result(
            run_id=ids["run_id"], result=late
        )
        assert str(draft.id) == before["sarDraft"]["sarDraftId"]
        await session.commit()
    after = await detail(client, ids)
    assert after["sarDraft"] == before["sarDraft"] and after["alert"] == before["alert"]
    assert after["actions"] == before["actions"] and after["submissions"] == before["submissions"]


async def test_current_flags_remain_after_manual_recovery_of_critical_case(case_api):
    client, _, sm = case_api
    ids = await seed_case(sm, sar=False, risk_band=RiskBand.CRITICAL)
    await pickup(client, ids)
    response = await client.patch(
        f"/api/v1/alerts/{ids['alert_id']}/sar",
        json={
            "expectedCaseVersion": 2,
            "expectedSarVersion": None,
            "content": "Recovered manually.",
        },
        headers=headers("analyst"),
    )
    assert response.status_code == 200
    d = await detail(client, ids)
    assert {item["flag"] for item in d["alert"]["reviewFlags"]} == {"critical_risk_band"}
    assert not d["alert"]["capabilities"]["dismiss"]["allowed"]


async def test_backend_queue_filters_use_canonical_ownership_and_independence(case_api):
    client, ids, _ = case_api

    def ids_in(response):
        return {row["alertId"] for row in response.json()["alerts"]}

    assert str(ids["alert_id"]) in ids_in(
        await client.get("/api/v1/alerts?queue=available", headers=headers("analyst"))
    )
    await pickup(client, ids)
    assert str(ids["alert_id"]) in ids_in(
        await client.get("/api/v1/alerts?queue=mine", headers=headers("maker-alias"))
    )
    submission, _ = await submit(client, ids)
    assert not ids_in(
        await client.get("/api/v1/alerts?queue=awaiting_review", headers=headers("maker-alias"))
    )
    assert str(ids["alert_id"]) in ids_in(
        await client.get("/api/v1/alerts?queue=awaiting_review", headers=headers("admin"))
    )
    assert (await decide(client, ids, submission, "return")).status_code == 200
    assert str(ids["alert_id"]) in ids_in(
        await client.get("/api/v1/alerts?queue=returned", headers=headers("analyst"))
    )
    assert not ids_in(await client.get("/api/v1/alerts?queue=returned", headers=headers("auditor")))
