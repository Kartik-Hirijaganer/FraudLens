"""Behavioral proofs for person-eligible dashboard queue totals before pagination."""

from __future__ import annotations

import uuid

from case_workflow_fakes import decide, headers, pickup, seed_case, submit

from fraudlens_backend.db.models.enums import AlertStatus
from fraudlens_backend.db.repositories import AlertRepository


async def test_review_totals_match_independence_filters_before_pagination(case_api):
    client, first, sm = case_api
    second = await seed_case(sm)
    own = await seed_case(sm)
    closed = await seed_case(sm)
    for ids, maker in ((first, "analyst"), (second, "analyst"), (own, "reviewer")):
        await pickup(client, ids, maker)
        await submit(client, ids, maker)
    await pickup(client, closed)
    submission, _ = await submit(client, closed)
    assert (await decide(client, closed, submission)).status_code == 200

    for persona, expected in (("reviewer", 2), ("admin", 3), ("maker-alias", 1)):
        response = await client.get(
            "/api/v1/alerts?queue=awaiting_review&limit=1", headers=headers(persona)
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == expected
        assert len(body["alerts"]) == 1
        assert body["alerts"][0]["capabilities"]["approve"]["allowed"]

    beyond = await client.get(
        "/api/v1/alerts?queue=awaiting_review&limit=1&offset=2", headers=headers("reviewer")
    )
    assert beyond.json() == {"alerts": [], "total": 2}
    incompatible = await client.get(
        "/api/v1/alerts?queue=awaiting_review&status=open", headers=headers("reviewer")
    )
    assert incompatible.json() == {"alerts": [], "total": 0}
    for persona in ("analyst", "auditor"):
        forbidden = await client.get(
            "/api/v1/alerts?queue=awaiting_review", headers=headers(persona)
        )
        assert forbidden.json() == {"alerts": [], "total": 0}

    all_cases = await client.get("/api/v1/alerts?limit=1", headers=headers("auditor"))
    assert all_cases.json()["total"] == 4
    async with sm() as session:
        assert await AlertRepository(session, uuid.uuid4()).count_alerts() == 0


async def test_queue_counts_follow_return_and_resubmission(case_api):
    client, ids, _ = case_api
    await pickup(client, ids)
    submission, _ = await submit(client, ids)
    response = await decide(client, ids, submission, "return")
    assert response.status_code == 200
    reviewer = await client.get("/api/v1/alerts?queue=awaiting_review", headers=headers("reviewer"))
    returned = await client.get("/api/v1/alerts?queue=returned", headers=headers("analyst"))
    assert reviewer.json() == {"alerts": [], "total": 0}
    assert returned.json()["total"] == 1
    assert returned.json()["alerts"][0]["status"] == AlertStatus.IN_REVIEW.value
    await submit(client, ids, key="resubmit")
    reviewer = await client.get("/api/v1/alerts?queue=awaiting_review", headers=headers("reviewer"))
    assert reviewer.json()["total"] == 1
