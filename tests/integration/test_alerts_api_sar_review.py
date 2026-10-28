"""The coordinated SAR API cutover and post-commit artifact failure/recovery contract."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from case_workflow_fakes import AGENCY, headers, seed_case
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from storage_fakes import _client, _demo_app

from fraudlens_backend.backends.storage import LocalFsStorage
from fraudlens_backend.db.models import Alert, CaseDecision, SarDraft, TrainingLabel
from fraudlens_backend.settings import AppSettings


async def prepare(client, ids):
    route = f"/api/v1/alerts/{ids['alert_id']}"
    pickup = await client.post(
        f"{route}/actions",
        json={"action": "assign", "expectedCaseVersion": 1},
        headers=headers("analyst"),
    )
    assert pickup.status_code == 200, pickup.text
    submitted = await client.post(
        f"{route}/submissions",
        json={
            "expectedCaseVersion": 2,
            "recommendation": "sar",
            "sarVersion": 1,
            "rationale": "Full evidence reviewed.",
        },
        headers={**headers("analyst"), "Idempotency-Key": "artifact-submit"},
    )
    assert submitted.status_code == 200, submitted.text
    return route, submitted.json()["submissionId"]


async def test_pdf_failure_is_durable_and_authorized_retry_recovers_complete_artifact(
    make_settings: Callable[..., AppSettings],
    file_db: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    engine, sm = file_db
    ids = await seed_case(sm)
    # More than one page, with a final narrative sentinel and a final citation sentinel.
    narrative = (
        "\n".join(f"Evidence line {index}." for index in range(250)) + "\nFINAL NARRATIVE SENTINEL"
    )
    async with sm() as session:
        draft = await session.get(SarDraft, ids["sar_id"])
        draft.content = narrative
        draft.citations = [{"citation": f"Reference {index}"} for index in range(100)]
        await session.commit()
    app = _demo_app(make_settings, engine, sm, storage_local_dir=str(tmp_path / "artifacts"))

    original_put = LocalFsStorage.put

    def fail(*args, **kwargs):
        raise OSError("Synthetic storage failure")

    monkeypatch.setattr(LocalFsStorage, "put", fail)
    async with _client(app) as client:
        route, sid = await prepare(client, ids)
        decision_route = f"{route}/submissions/{sid}/decision"
        decision = await client.post(
            decision_route,
            json={
                "expectedCaseVersion": 3,
                "decision": "approve",
                "reason": "Independent checker accepts evidence.",
                "outcome": "confirmed_fraud",
            },
            headers={**headers("reviewer"), "Idempotency-Key": "artifact-approve"},
        )
        assert decision.status_code == 200, decision.text
        d = (await client.get(route, headers=headers("reviewer"))).json()
        assert d["alert"]["status"] == "resolved" and d["sarDraft"]["status"] == "approved"
        assert d["submissions"][0]["pdfStatus"] == "failed"
        assert d["submissions"][0]["canRetryPdf"]
        pdf_route = f"{route}/submissions/{sid}/pdf"
        assert (await client.get(pdf_route, headers=headers("auditor"))).status_code == 409
        assert (
            await client.post(f"{pdf_route}/retry", headers=headers("analyst"))
        ).status_code == 403
        monkeypatch.setattr(LocalFsStorage, "put", original_put)
        retry = await client.post(f"{pdf_route}/retry", headers=headers("admin"))
        assert retry.status_code == 200
        pdf = await client.get(pdf_route, headers=headers("auditor"))
        assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
        assert b"FINAL NARRATIVE SENTINEL" in pdf.content and b"Reference 99" in pdf.content
        assert b"truncated" not in pdf.content and b"/Count 8" in pdf.content
        d = (await client.get(route, headers=headers("reviewer"))).json()
        assert (
            d["submissions"][0]["pdfStatus"] == "ready" and not d["submissions"][0]["canRetryPdf"]
        )
        assert (await client.post(f"{pdf_route}/retry", headers=headers("reviewer"))).json()[
            "pdfStatus"
        ] == "ready"
        assert (
            await client.get(
                f"/api/v1/alerts/{ids['transaction_id']}/submissions/{sid}/pdf",
                headers=headers("admin"),
            )
        ).status_code == 404
    async with sm() as session:
        decisions = (
            (await session.execute(select(CaseDecision).where(CaseDecision.agency_id == AGENCY)))
            .scalars()
            .all()
        )
        labels = (
            (await session.execute(select(TrainingLabel).where(TrainingLabel.agency_id == AGENCY)))
            .scalars()
            .all()
        )
        assert len(decisions) == len(labels) == 1
        assert decisions[0].decision == "approve"


@pytest.mark.parametrize("decision", ["approve", "return", "decline"])
async def test_checker_requires_reason_key_and_compatible_outcome(
    make_settings, db_engine, db_sessionmaker, decision
):
    ids = await seed_case(db_sessionmaker)
    async with _client(_demo_app(make_settings, db_engine, db_sessionmaker)) as client:
        route, sid = await prepare(client, ids)
        endpoint = f"{route}/submissions/{sid}/decision"
        body = {"expectedCaseVersion": 3, "decision": decision, "reason": " "}
        if decision != "return":
            body["outcome"] = "confirmed_fraud" if decision == "approve" else "benign"
        assert (
            await client.post(
                endpoint, json=body, headers={**headers("reviewer"), "Idempotency-Key": "blank"}
            )
        ).status_code == 422
        body["reason"] = "Reviewed."
        assert (
            await client.post(endpoint, json=body, headers=headers("reviewer"))
        ).status_code == 422
        assert (
            await client.post(
                endpoint,
                json={**body, "editedContent": "Bypass"},
                headers={**headers("reviewer"), "Idempotency-Key": "inline-edit"},
            )
        ).status_code == 422
        assert (
            await client.post(
                f"{route}/sar/review", json={"decision": "approve"}, headers=headers("admin")
            )
        ).status_code == 404
    async with db_sessionmaker() as session:
        alert = await session.get(Alert, ids["alert_id"])
        assert alert.status.value == "escalated" and alert.case_version == 3
        assert (
            not (
                await session.execute(select(CaseDecision).where(CaseDecision.agency_id == AGENCY))
            )
            .scalars()
            .all()
        )
