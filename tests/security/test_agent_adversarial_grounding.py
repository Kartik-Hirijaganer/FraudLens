"""Adversarial agent grounding, forged-reference, and citation-integrity tests."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from agent_fakes import (
    _AUTHORITY_REF,
    _CITATION,
    _NEAR_CITATION,
    _config,
    _prompts,
    _RoleRuntime,
)
from agent_security_fakes import (
    _draft,
    _graph_outcomes,
    _sar_input,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.agents.checks import evaluate_draft_checks
from fraudlens_backend.agents.config import AgentRole
from fraudlens_backend.agents.contracts import (
    AgentExecutionRecord,
)
from fraudlens_backend.agents.graph import build_agent_graph
from fraudlens_backend.db.models import (
    Agency,
    Alert,
    AlertStatus,
    AnalysisRun,
    RunStatus,
    Severity,
    Transaction,
)
from fraudlens_backend.db.repositories import AgentExecutionRepository
from fraudlens_backend.sar.budget import BudgetGuard
from fraudlens_backend.sar.drafter_multi_agent import MultiAgentSarDrafter
from fraudlens_backend.sar.schema import parse_and_ground
from fraudlens_ml.sar import (
    SarCitation,
    SarDraftStatus,
)


async def test_forged_refs_force_one_revision_and_final_claims_resolve_to_persisted_evidence(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    agency_id = uuid.uuid4()
    run_id = uuid.uuid4()
    transaction_id = uuid.uuid4()
    async with db_sessionmaker() as session:
        session.add(Agency(id=agency_id, name="Acceptance", slug=f"acceptance-{agency_id.hex}"))
        session.add(
            Transaction(
                id=transaction_id,
                agency_id=agency_id,
                external_id="security-acceptance",
                amount=Decimal("9500.00"),
                currency="USD",
                occurred_at=datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
                origin_account="masked-a",
                dest_account="masked-b",
                channel="wire",
                country="US",
                features={},
                feature_hash="security-acceptance-hash",
            )
        )
        session.add(
            AnalysisRun(
                id=run_id,
                agency_id=agency_id,
                transaction_id=transaction_id,
                status=RunStatus.RUNNING,
                workflow_mode="multi_agent",
                graph_version="agents-v1",
            )
        )
        session.add(
            Alert(
                agency_id=agency_id,
                transaction_id=transaction_id,
                run_id=run_id,
                status=AlertStatus.OPEN,
                severity=Severity.HIGH,
                review_flags=[],
            )
        )
        await session.commit()

    runtime = _RoleRuntime(_graph_outcomes())

    async def record_execution(record: AgentExecutionRecord) -> None:
        async with db_sessionmaker() as session:
            await AgentExecutionRepository(session, agency_id).save_from_record(
                run_id=run_id,
                record=record,
            )
            await session.commit()

    config = _config(max_revisions=1)
    prompts = _prompts()
    graph = build_agent_graph(
        runtime=runtime,
        config=config,
        prompts=prompts,
        run_id=run_id,
        record_execution=record_execution,
    )
    drafter = MultiAgentSarDrafter(
        graph=graph,
        config=config,
        prompts=prompts,
        budget=BudgetGuard(session_limit_usd=Decimal("1")),
    )

    events = [event async for event in drafter.draft(_sar_input(transaction_id))]
    terminal = events[-1].result

    assert terminal is not None and terminal.status is SarDraftStatus.DRAFT
    assert terminal.revision_count == 1
    assert terminal.structured is not None
    assert terminal.structured.claims[0].evidence_refs == (_AUTHORITY_REF,)
    assert terminal.structured.claims[0].citation_ids == (_CITATION,)
    assert tuple(citation.citation for citation in terminal.citations) == (_CITATION,)
    assert runtime.calls.count((AgentRole.SAR_WRITER, 1)) == 1
    assert runtime.calls.count((AgentRole.SAR_WRITER, 2)) == 1
    assert runtime.calls.count((AgentRole.COMPLIANCE_REVIEWER, 2)) == 1

    async with db_sessionmaker() as session:
        rows = list(await AgentExecutionRepository(session, agency_id).list_for_run(run_id))
        alert_status = (
            await session.execute(select(Alert.status).where(Alert.run_id == run_id))
        ).scalar_one()
    evidence_row = next(row for row in rows if row.agent.value == "evidence_investigator")
    persisted_refs = {
        item["result"]["hits"][0]["evidenceRef"]
        for item in evidence_row.tool_calls
        if item["status"] == "completed" and item["result"] is not None
    }
    assert set(terminal.structured.claims[0].evidence_refs) <= persisted_refs
    assert alert_status is AlertStatus.OPEN
    assert terminal.status.value not in {"approved", "resolved", "dismissed"}

    single_writer_json = json.dumps(
        {
            "subject": "Potential structuring",
            "narrative": "Synthetic activity warrants human review.",
            "sections": [],
            "citedRegulations": [_CITATION, _NEAR_CITATION],
            "recommendedAction": "Escalate for human review.",
        }
    )
    baseline_content, baseline_citations = parse_and_ground(
        single_writer_json,
        _sar_input(transaction_id).citations,
    )
    assert baseline_content.cited_regulations == terminal.structured.cited_regulations
    assert tuple(item.citation for item in baseline_citations) == tuple(
        item.citation for item in terminal.citations
    )


def test_deterministic_checks_reject_near_miss_citations_and_unpersisted_evidence_refs() -> None:
    content = _draft(evidence_ref="rule-hit:forged:0", citation_id=_NEAR_CITATION)
    available = (
        SarCitation(
            citation=_CITATION,
            title="Structuring",
            source="FinCEN",
            snippet="Governed synthetic excerpt.",
        ),
    )

    checks = evaluate_draft_checks(
        content,
        available,
        available_evidence_refs={_AUTHORITY_REF},
    )

    assert checks.passed is False
    assert checks.evidence_refs_are_available is False
    assert checks.unresolved_evidence_refs == ("rule-hit:forged:0",)
    assert checks.unsupported_claim_indexes == (0,)
    assert checks.fabricated_citation_ids == (_NEAR_CITATION,)
