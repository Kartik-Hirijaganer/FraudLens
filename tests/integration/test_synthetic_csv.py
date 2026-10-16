"""Summary: Exact-fixture CSV trust, immutable provenance and provider-free live-drafter acceptance.

Key classes:
- RecordingLiveDrafter: run the real LLM client and drafter with an SDK adapter double.

Key functions:
- (none)

Notes:
- No API credentials or network calls; only the model response is scripted.
- Rules/scoring ports are fixed; worker, evidence projection, gate, persistence and review are real.
"""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from investigation_fakes import _client, _demo_app, _seed_demo_transaction
from llm_fakes import FakeAdapter
from pipeline_fakes import FakeExplainerPort, FakeRulesPort, FakeScorerPort
from portfolio_demo_identity import DEMO_AGENCY_ID, demo_user_id
from sar_drafts import fabricated_citation_json, gate_passing_json
from sqlalchemy import select

import fraudlens_backend.runs.worker as worker_module
import fraudlens_backend.services.sar_regeneration as regeneration
import fraudlens_backend.services.transaction_ingest as ingestion
import ingest_rag
from fraudlens_backend.db.models import (
    AnalysisRun,
    AuditLog,
    SarDraft,
    Transaction,
    TransactionSource,
    UserRole,
)
from fraudlens_backend.pipeline_wiring import build_pipeline_deps
from fraudlens_backend.runs.worker import DurableRunWorker
from fraudlens_backend.sar.factory import build_sar_drafter
from fraudlens_llm import LlmClient


class RecordingLiveDrafter:
    """Exercise production provider requests against a recording in-process adapter."""

    def __init__(self, settings: Any, *, invalid: bool) -> None:
        self.settings = settings.model_copy(update={"llm_mode": "live"})
        self.invalid = invalid
        self.adapters: list[FakeAdapter] = []

    async def draft(self, sar_input: Any) -> Any:
        adapter = FakeAdapter(
            text=(fabricated_citation_json if self.invalid else gate_passing_json)(sar_input),
            fail_once=not self.invalid,
        )
        self.adapters.append(adapter)
        client = LlmClient.from_settings()
        client._adapters["openrouter"] = adapter
        drafter = build_sar_drafter(self.settings, client=client)
        async for event in drafter.draft(sar_input):
            yield event


async def test_download_requires_ingest_permission_and_mock_mode_is_honest(
    make_settings: Any, db_engine: Any, db_sessionmaker: Any
) -> None:
    await _seed_demo_transaction(db_sessionmaker)
    auditor = _demo_app(make_settings, db_engine, db_sessionmaker, auth_dev_bypass_role="auditor")
    async with _client(auditor) as client:
        assert (await client.get("/api/v1/transactions/template")).status_code == 403
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        template = (await client.get("/api/v1/transactions/template")).json()["content"]
        report = (
            await client.post(
                "/api/v1/transactions/upload",
                content=template,
                headers={"Content-Type": "text/csv"},
            )
        ).json()
        assert report["trustedSynthetic"] is True and report["draftingMode"] == "mock"


@pytest.mark.parametrize("change", ["amount", "newline", "identifier"])
async def test_modified_demo_file_stays_untrusted(
    change: str, make_settings: Any, db_engine: Any, db_sessionmaker: Any
) -> None:
    await _seed_demo_transaction(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    original = ingestion.synthetic_csv_template()
    modified = {
        "amount": original.replace(b"100000.00", b"100001.00"),
        "newline": original.replace(b"\n", b"\r\n"),
        "identifier": original.replace(b"DEMO-ACCOUNT-A", b"OTHER-ACCOUNT-A"),
    }[change]
    async with _client(app) as client:
        response = await client.post(
            "/api/v1/transactions/upload", content=modified, headers={"Content-Type": "text/csv"}
        )
    assert response.status_code == 202
    assert response.json()["trustedSynthetic"] is False
    assert response.json()["draftingMode"] == "template"
    async with db_sessionmaker() as session:
        rows = (
            (
                await session.execute(
                    select(Transaction).where(
                        Transaction.agency_id == DEMO_AGENCY_ID,
                        Transaction.source == TransactionSource.API_UPLOAD,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 3


async def test_duplicate_ids_never_upgrade_existing_upload_provenance(
    make_settings: Any, db_engine: Any, db_sessionmaker: Any
) -> None:
    await _seed_demo_transaction(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    original = ingestion.synthetic_csv_template()
    async with _client(app) as client:
        for body, trusted in ((original.replace(b"\n", b"\r\n"), False), (original, True)):
            response = await client.post(
                "/api/v1/transactions/upload", content=body, headers={"Content-Type": "text/csv"}
            )
            assert response.json()["trustedSynthetic"] is trusted
        assert response.json()["accepted"] == 0
        assert response.json()["duplicates"] == 3
    async with db_sessionmaker() as session:
        rows = (
            (
                await session.execute(
                    select(Transaction).where(
                        Transaction.agency_id == DEMO_AGENCY_ID,
                        Transaction.source == TransactionSource.SYNTHETIC_GENERATOR,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows == []


async def test_missing_fixture_fails_closed_without_blocking_local_uploads(
    make_settings: Any, db_engine: Any, db_sessionmaker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = ingestion.synthetic_csv_template()
    await _seed_demo_transaction(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    monkeypatch.setattr(ingestion, "synthetic_csv_template", Mock(side_effect=OSError))
    monkeypatch.setattr(
        "fraudlens_backend.api.v1.transactions.synthetic_csv_template", Mock(side_effect=OSError)
    )
    async with _client(app) as client:
        assert (await client.get("/api/v1/transactions/template")).status_code == 503
        response = await client.post(
            "/api/v1/transactions/upload", content=original, headers={"Content-Type": "text/csv"}
        )
        assert response.json()["trustedSynthetic"] is False


@pytest.mark.parametrize("invalid", [False, True])
async def test_trusted_csv_through_real_live_drafter_gate_and_reviewer(
    invalid: bool,
    make_settings: Any,
    file_db: Any,
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_file_engine, sm = file_db
    await _seed_demo_transaction(sm)
    settings = make_settings(rag_index_dir=str(sandbox / "index"))
    ingest_rag.ingest_corpus(
        settings, chunk_size=ingest_rag.DEFAULT_CHUNK_SIZE, overlap=ingest_rag.DEFAULT_CHUNK_OVERLAP
    )
    app = _demo_app(
        make_settings,
        db_file_engine,
        sm,
        rag_index_dir=settings.rag_index_dir,
        run_execution_mode="worker",
        multi_agent_sar_enabled=False,
        llm_mode="live",
    )
    live = RecordingLiveDrafter(app.state.settings, invalid=invalid)
    components = replace(app.state.pipeline_components, drafter=live)
    scorer = FakeScorerPort()
    score = Mock(wraps=scorer.score)
    monkeypatch.setattr(scorer, "score", score)
    embed = Mock(side_effect=AssertionError("uploaded evidence must use local retrieval"))
    monkeypatch.setattr(components.retriever, "retrieve", embed)

    async def deps(**kwargs: Any) -> Any:
        kwargs["settings"] = kwargs["settings"].model_copy(update={"llm_mode": "live"})
        bound = await build_pipeline_deps(**kwargs)
        return replace(bound, rules=FakeRulesPort(), scorer=scorer, explainer=FakeExplainerPort())

    monkeypatch.setattr(worker_module, "build_pipeline_deps", deps)
    async with _client(app) as client:
        # Unknown facts sharing the public fixture's account ids cannot become trusted history.
        unverified = await client.post(
            "/api/v1/transactions",
            json={
                "externalId": "UNVERIFIED-HISTORY",
                "amount": "91357.92",
                "currency": "USD",
                "occurredAt": "2026-10-21T11:00:00Z",
                "originAccount": "DEMO-ACCOUNT-A",
                "destAccount": "DEMO-ACCOUNT-B",
                "channel": "wire",
                "country": "US",
            },
        )
        assert unverified.status_code == 201
        template = (await client.get("/api/v1/transactions/template")).json()["content"]
        assert template.encode() == ingestion.synthetic_csv_template()
        report = (
            await client.post(
                "/api/v1/transactions/upload",
                content=template,
                headers={"Content-Type": "text/csv"},
            )
        ).json()
        assert report["trustedSynthetic"] is True and report["draftingMode"] == "ai"
        assert report["accepted"] == 3
    worker = DurableRunWorker(
        sessionmaker=sm,
        components=components,
        settings=app.state.settings,
        worker_id="synthetic-csv",
        heartbeat_file=sandbox / "heartbeat",
    )
    for _ in range(report["accepted"]):
        await worker.run_once()
    embed.assert_not_called()
    assert score.call_count == 3
    for call in score.call_args_list:
        context = call.args[0]
        assert all(
            item.amount != Decimal("91357.92")
            for item in (*context.history, *context.counterparty_history)
        )
    assert len(live.adapters) == 3
    requests = json.dumps(
        [
            message.model_dump(mode="json")
            for adapter in live.adapters
            for call in adapter.stream_generate_calls
            for message in call
        ]
    )
    assert "DEMO-ACCOUNT" not in requests and "DEMO-CSV" not in requests
    async with sm() as session:
        drafts = (
            (
                await session.execute(
                    select(SarDraft).where(
                        SarDraft.agency_id == DEMO_AGENCY_ID,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(drafts) == 3
        assert all(d.model_id not in {"template", "mock"} for d in drafts)
        assert all(d.quality_status == ("failed" if invalid else "passed") for d in drafts)
        draft = drafts[0]
        run_id = str(draft.run_id)
        transactions = (
            (
                await session.execute(
                    select(Transaction).where(
                        Transaction.agency_id == DEMO_AGENCY_ID,
                        Transaction.source == TransactionSource.SYNTHETIC_GENERATOR,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(transactions) == 3
        assert all(t.origin_account == t.dest_account == "[WITHHELD]" for t in transactions)
        assert all(t.external_id.startswith("upload-") for t in transactions)
    async with _client(app) as client:
        snapshot = (await client.get(f"/api/v1/investigations/{run_id}")).json()
        assert snapshot["status"] == "completed" and snapshot["topFeatures"] and snapshot["alertId"]
        alert_id = snapshot["alertId"]
        before = len(live.adapters)
        await client.get(f"/api/v1/investigations/{run_id}")
        assert len(live.adapters) == before
        replay = (
            await client.post(
                "/api/v1/transactions/upload",
                content=template,
                headers={"Content-Type": "text/csv"},
            )
        ).json()
        assert replay["accepted"] == 0 and replay["duplicates"] == 3
    if invalid:
        assert snapshot["sarStatus"] == "failed"
        return
    monkeypatch.setattr(regeneration, "build_sar_drafter", lambda _settings: live)
    async with _client(app) as client:
        regenerated = await client.post(f"/api/v1/investigations/{run_id}/sar/regenerate")
        assert regenerated.status_code == 200 and regenerated.json()["version"] == 2
        assert regenerated.json()["qualityStatus"] == "passed", regenerated.json()
    analyst = _demo_app(make_settings, db_file_engine, sm, auth_dev_bypass_role="analyst")
    reviewer = _demo_app(make_settings, db_file_engine, sm, auth_dev_bypass_role="reviewer")
    async with _client(analyst) as client:
        assert (
            await client.post(
                f"/api/v1/alerts/{alert_id}/sar/review",
                json={"decision": "approve"},
            )
        ).status_code == 403
    async with _client(reviewer) as client:
        approved = await client.post(
            f"/api/v1/alerts/{alert_id}/sar/review", json={"decision": "approve"}
        )
        assert approved.status_code == 200, approved.json()
        detail = (await client.get(f"/api/v1/alerts/{alert_id}")).json()
        assert detail["sarDraft"]["status"] == "approved"
        assert detail["sarDraft"]["draftingMode"] == "ai"
    async with sm() as session:
        runs = (
            (
                await session.execute(
                    select(AnalysisRun).where(AnalysisRun.agency_id == DEMO_AGENCY_ID)
                )
            )
            .scalars()
            .all()
        )
        assert len(runs) == 3
        audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.agency_id == DEMO_AGENCY_ID,
                    AuditLog.action == "sar.approve",
                )
            )
        ).scalar_one()
        assert audit.actor_id == demo_user_id(UserRole.REVIEWER)
