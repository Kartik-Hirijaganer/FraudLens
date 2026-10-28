"""Portable acceptance proofs for identity, immutable rounds, atomic decisions and API cutover."""

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
from sqlalchemy import select

from fraudlens_backend.db.models import (
    AlertAction,
    AuditLog,
    CaseContributor,
    CaseDecision,
    CaseSubmission,
    TrainingLabel,
    User,
)
from fraudlens_backend.db.models.enums import UserRole
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.services.person_identity import resolve_actor
from fraudlens_core import RiskBand


async def test_independent_approval_is_atomic_and_frozen(case_api):
    client, ids, sm = case_api
    await pickup(client, ids)
    result, body = await submit(client, ids)
    submitted = await detail(client, ids)
    assert submitted["alert"]["status"] == "escalated"
    assert submitted["submissions"][0]["rationale"] == body["rationale"]
    assert submitted["submissions"][0]["sarDraft"]["version"] == 1
    response = await decide(client, ids, result)
    assert response.status_code == 200, response.text
    d = await detail(client, ids)
    assert d["alert"]["status"] == "resolved"
    assert d["sarDraft"]["status"] == "approved"
    assert d["submissions"][0]["reason"] == "Independent evidence review complete."
    async with sm() as session:
        assert await count(session, CaseDecision) == await count(session, TrainingLabel) == 1
        label = (
            await session.execute(select(TrainingLabel).where(TrainingLabel.agency_id == AGENCY))
        ).scalar_one()
        assert label.label.value == "confirmed_fraud"
        assert await count(session, CaseContributor) == 1


@pytest.mark.parametrize("maker", ["analyst", "reviewer", "admin"])
async def test_person_independence_including_linked_admin_and_role_changes(case_api, maker):
    client, ids, sm = case_api
    await pickup(client, ids, maker)
    result, _ = await submit(client, ids, maker)
    before = await detail(client, ids, maker)
    candidates = [maker] + (["maker-alias"] if maker == "analyst" else [])
    async with sm() as session:
        user = await session.get(User, account(maker))
        user.role = UserRole.ADMIN
        await session.commit()
    for persona in candidates:
        for decision in ("approve", "decline", "return"):
            response = await decide(
                client, ids, result, decision, persona, f"blocked-{persona}-{decision}"
            )
            assert response.status_code == 403, response.text
    after = await detail(client, ids, maker)
    assert after["sarDraft"] == before["sarDraft"]
    assert after["actions"] == before["actions"]
    assert after["submissions"] == before["submissions"]
    assert after["alert"]["caseVersion"] == before["alert"]["caseVersion"]
    async with sm() as session:
        assert await count(session, CaseDecision) == await count(session, TrainingLabel) == 0
    independent = "admin" if maker == "reviewer" else "reviewer"
    assert (await decide(client, ids, result, persona=independent)).status_code == 200


async def test_return_revise_resubmit_preserves_rounds_and_checker_eligibility(case_api):
    client, ids, sm = case_api
    await pickup(client, ids)
    first, _ = await submit(client, ids)
    d = await detail(client, ids, "reviewer")
    response = await client.post(
        f"/api/v1/alerts/{ids['alert_id']}/actions",
        json={
            "action": "comment",
            "note": "Clarify the transaction rationale.",
            "expectedCaseVersion": d["alert"]["caseVersion"],
        },
        headers=headers("reviewer"),
    )
    assert response.status_code == 200
    assert (await decide(client, ids, first, "return")).status_code == 200
    d = await detail(client, ids)
    assert d["alert"]["assignedTo"] == str(account("analyst"))
    assert d["sarDraft"]["version"] == 2 and d["sarDraft"]["status"] == "draft"
    assert (
        d["submissions"][0]["sarDraft"]["content"] == "Frozen original narrative. Final sentinel."
    )
    response = await client.patch(
        f"/api/v1/alerts/{ids['alert_id']}/sar",
        json={
            "expectedCaseVersion": d["alert"]["caseVersion"],
            "expectedSarVersion": 2,
            "content": "Revised with corroborating evidence.",
        },
        headers=headers("analyst"),
    )
    assert response.status_code == 200
    second, _ = await submit(client, ids, key="resubmit")
    assert second["submissionId"] != first["submissionId"]
    d = await detail(client, ids, "reviewer")
    assert [row["round"] for row in d["submissions"]] == [2, 1]
    assert d["alert"]["capabilities"]["approve"]["allowed"]
    assert (await decide(client, ids, second, key="approve-second")).status_code == 200
    async with sm() as session:
        contributors = (
            (
                await session.execute(
                    select(CaseContributor).where(CaseContributor.agency_id == AGENCY)
                )
            )
            .scalars()
            .all()
        )
        assert {row.person_id for row in contributors} == {STORY.persona("analyst").person_id}
        assert await count(session, CaseSubmission) == await count(session, CaseDecision) == 2
        assert await count(session, TrainingLabel) == 1


async def test_working_drafts_lock_and_old_mutation_paths_are_absent(case_api):
    client, ids, _ = case_api
    await pickup(client, ids)
    result, _ = await submit(client, ids)
    before = await detail(client, ids)
    version = before["alert"]["caseVersion"]
    for response in [
        await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions",
            json={"action": "assign", "expectedCaseVersion": version},
            headers=headers("admin"),
        ),
        await client.patch(
            f"/api/v1/alerts/{ids['alert_id']}/sar",
            json={
                "content": "Forbidden edit",
                "expectedCaseVersion": version,
                "expectedSarVersion": 1,
            },
            headers=headers("analyst"),
        ),
        await client.post(
            f"/api/v1/investigations/{ids['run_id']}/sar/regenerate",
            json={"expectedCaseVersion": version},
            headers=headers("analyst"),
        ),
    ]:
        assert response.status_code == 403, response.text
    old = await client.post(
        f"/api/v1/alerts/{ids['alert_id']}/sar/review", json={"decision": "approve"}
    )
    assert old.status_code == 404
    for action in ("resolve", "escalate"):
        response = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions",
            json={"action": action, "expectedCaseVersion": version},
        )
        assert response.status_code == 422
    assert (await detail(client, ids))["actions"] == before["actions"]
    assert (await decide(client, ids, result, "decline")).status_code == 200
    assert (await detail(client, ids))["sarDraft"]["status"] == "rejected"


async def test_idempotency_stale_and_missing_reasons_leave_no_partial_writes(case_api):
    client, ids, sm = case_api
    await pickup(client, ids)
    first, body = await submit(client, ids)
    replay = await client.post(
        f"/api/v1/alerts/{ids['alert_id']}/submissions",
        json=body,
        headers={**headers("analyst"), "Idempotency-Key": "submit"},
    )
    assert replay.json() == first
    bad = await client.post(
        f"/api/v1/alerts/{ids['alert_id']}/submissions",
        json={**body, "rationale": "different"},
        headers={**headers("analyst"), "Idempotency-Key": "submit"},
    )
    assert bad.status_code == 409
    route = f"/api/v1/alerts/{ids['alert_id']}/submissions/{first['submissionId']}/decision"
    for extra in [{"reason": " "}, {"outcome": "benign"}, {"editedContent": "inline edit"}]:
        response = await client.post(
            route,
            json={
                "expectedCaseVersion": first["caseVersion"],
                "decision": "approve",
                "reason": "reviewed",
                "outcome": "confirmed_fraud",
                **extra,
            },
            headers={**headers("reviewer"), "Idempotency-Key": "bad"},
        )
        assert response.status_code == 422
    stale = await client.post(
        route,
        json={
            "expectedCaseVersion": 1,
            "decision": "approve",
            "reason": "reviewed",
            "outcome": "confirmed_fraud",
        },
        headers={**headers("reviewer"), "Idempotency-Key": "stale"},
    )
    assert stale.status_code == 409
    valid = {
        "expectedCaseVersion": first["caseVersion"],
        "decision": "decline",
        "reason": "No reportable concern.",
        "outcome": "false_positive",
    }
    response = await client.post(
        route, json=valid, headers={**headers("reviewer"), "Idempotency-Key": "final"}
    )
    assert response.status_code == 200
    retry = await client.post(
        route, json=valid, headers={**headers("reviewer"), "Idempotency-Key": "final"}
    )
    assert retry.json() == response.json()
    async with sm() as session:
        assert (
            await count(session, CaseSubmission)
            == await count(session, CaseDecision)
            == await count(session, TrainingLabel)
            == 1
        )


@pytest.mark.parametrize(
    "band,probability,sar,expected",
    [
        (RiskBand.CRITICAL, 0.91, True, "critical_risk_band"),
        (RiskBand.HIGH, 0.5, True, "low_model_confidence"),
        (RiskBand.HIGH, 0.91, False, "sar_unavailable"),
    ],
)
async def test_current_flags_block_dismissal_even_without_sar(
    case_api, band, probability, sar, expected
):
    client, _, sm = case_api
    ids = await seed_case(sm, risk_band=band, probability=probability, sar=sar)
    d = await pickup(client, ids)
    response = await client.post(
        f"/api/v1/alerts/{ids['alert_id']}/actions",
        json={
            "action": "dismiss",
            "note": "No suspicious activity.",
            "expectedCaseVersion": d["caseVersion"],
        },
        headers=headers("analyst"),
    )
    assert response.status_code == 403
    d = await detail(client, ids)
    assert expected in {item["flag"] for item in d["alert"]["reviewFlags"]}
    submission, _ = await submit(client, ids, recommendation="no_sar")
    assert (await decide(client, ids, submission)).status_code == 403
    assert (await decide(client, ids, submission, "decline")).status_code == 200


async def test_manual_recovery_clears_only_current_sar_flag_then_dismisses(case_api):
    client, _, sm = case_api
    ids = await seed_case(sm, sar=False)
    d = await pickup(client, ids)
    response = await client.patch(
        f"/api/v1/alerts/{ids['alert_id']}/sar",
        json={
            "content": "Manual evidence reviewed. No reportable concern.",
            "expectedCaseVersion": d["caseVersion"],
            "expectedSarVersion": None,
        },
        headers=headers("analyst"),
    )
    assert response.status_code == 200
    d = await detail(client, ids)
    assert not d["alert"]["reviewFlags"]
    response = await client.post(
        f"/api/v1/alerts/{ids['alert_id']}/actions",
        json={
            "action": "dismiss",
            "note": "Reviewed and no suspicious activity.",
            "expectedCaseVersion": d["alert"]["caseVersion"],
        },
        headers=headers("analyst"),
    )
    assert response.status_code == 200
    d = await detail(client, ids)
    assert d["sarDraft"]["status"] == "withdrawn"
    assert d["sarDraft"]["content"].startswith("Manual evidence")


async def test_missing_identity_auditor_and_cross_tenant_fail_closed(case_api):
    client, ids, sm = case_api
    for persona in ("auditor",):
        response = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions",
            json={"action": "assign", "expectedCaseVersion": 1},
            headers=headers(persona),
        )
        assert response.status_code == 403
    async with sm() as session:
        user = User(
            agency_id=AGENCY,
            email="unmapped@workflow.test",
            display_name="Unmapped",
            role=UserRole.ADMIN,
        )
        session.add(user)
        await session.flush()
        user_id = user.id
        await session.commit()

    async with sm() as session:
        with pytest.raises(AppError, match="person_mapping_required"):
            await resolve_actor(session, AGENCY, user_id)
    response = await client.get(f"/api/v1/alerts/{uuid.uuid4()}", headers=headers("analyst"))
    assert response.status_code == 404


async def test_reasons_and_narratives_are_masked_and_audit_has_no_free_text(case_api):
    client, ids, sm = case_api
    d = await pickup(client, ids)
    text = "Reference SSN 123-45-6789."
    response = await client.patch(
        f"/api/v1/alerts/{ids['alert_id']}/sar",
        json={"content": text, "expectedCaseVersion": d["caseVersion"], "expectedSarVersion": 1},
        headers=headers("analyst"),
    )
    assert response.status_code == 200
    assert "123-45-6789" not in response.text
    d = await detail(client, ids)
    response = await client.post(
        f"/api/v1/alerts/{ids['alert_id']}/actions",
        json={"action": "comment", "note": text, "expectedCaseVersion": d["alert"]["caseVersion"]},
        headers=headers("reviewer"),
    )
    assert response.status_code == 200
    async with sm() as session:
        actions = (
            (await session.execute(select(AlertAction).where(AlertAction.agency_id == AGENCY)))
            .scalars()
            .all()
        )
        assert any(
            row.action.value == "comment" and "123-45-6789" not in (row.note or "")
            for row in actions
        )
        audit = (
            (await session.execute(select(AuditLog).where(AuditLog.agency_id == AGENCY)))
            .scalars()
            .all()
        )
        assert all(
            "123-45-6789" not in str(row.meta) and "Reference SSN" not in str(row.meta)
            for row in audit
        )
