"""Phase 9 adversarial security gate for the bounded multi-agent SAR workflow.

The suite drives the production guardrails, graph, tenant-scoped tools, persistence replay,
idempotency, and SSE replay seams with synthetic hostile inputs. It intentionally performs no
live provider, Infisical, Supabase, or network access.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from agent_fakes import (
    _AUTHORITY_REF,
    _CITATION,
    _INJECTION,
    _NEAR_CITATION,
)
from pydantic import BaseModel, JsonValue
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from fraudlens_backend.agents.config import AgentRole
from fraudlens_backend.agents.contracts import (
    AgentExecutionRecord,
    AgentToolCallRecord,
    AgentToolCallStatus,
    EvidenceBrief,
    EvidenceFinding,
    RegulatoryBrief,
    RegulatoryFinding,
    ReviewDecision,
    ReviewVerdict,
)
from fraudlens_backend.agents.prompts import AgentPromptTemplate
from fraudlens_backend.agents.runtime import AgentRuntime
from fraudlens_backend.db.models import (
    Agency,
    Alert,
    AlertStatus,
    AnalysisResult,
    AnalysisRun,
    RunStatus,
    Severity,
    Transaction,
)
from fraudlens_backend.pipeline_wiring import RunManager
from fraudlens_core import RiskBand
from fraudlens_ml.sar import (
    SarCitation,
    SarClaim,
    SarDraftContent,
    SarInput,
)


async def _execute_role(
    runtime: AgentRuntime,
    role: AgentRole,
    response_model: type[BaseModel],
) -> AgentExecutionRecord:
    return await runtime.execute(
        agent=role,
        prompt=AgentPromptTemplate.load(role, "v1"),
        user_content="Assess only supplied synthetic evidence.",
        response_model=response_model,
    )


def _evidence_json() -> str:
    return EvidenceBrief(
        summary="Persisted evidence requires human review.",
        findings=(
            EvidenceFinding(
                statement="A persisted rule matched.",
                evidence_refs=(_AUTHORITY_REF,),
            ),
        ),
    ).model_dump_json(by_alias=True)


def _sar_input(transaction_id: uuid.UUID) -> SarInput:
    return SarInput(
        agency_id="security-agency",
        transaction_id=str(transaction_id),
        risk_band=RiskBand.HIGH,
        fraud_probability=0.91,
        amount=Decimal("9500.00"),
        currency="USD",
        country="US",
        channel="wire",
        model_version="security-model",
        rules_version="security-rules",
        rag_version="security-rag",
        citations=(
            SarCitation(
                citation=_CITATION,
                title="Structuring",
                source="FinCEN",
                snippet="Governed synthetic excerpt.",
            ),
        ),
    )


def _draft(*, evidence_ref: str, citation_id: str) -> SarDraftContent:
    return SarDraftContent(
        subject="Potential structuring",
        narrative="Persisted synthetic activity warrants human review.",
        claims=(
            SarClaim(
                statement="A persisted rule matched.",
                evidence_refs=(evidence_ref,),
                citation_ids=(citation_id,),
            ),
        ),
        cited_regulations=(citation_id,),
        recommended_action="Escalate for human review.",
    )


def _graph_outcomes() -> dict[
    AgentRole, Sequence[tuple[BaseModel, tuple[AgentToolCallRecord, ...]]]
]:
    evidence_call = AgentToolCallRecord(
        call_id="evidence-1",
        name="rule_hits",
        status=AgentToolCallStatus.COMPLETED,
        result={"hits": [{"evidenceRef": _AUTHORITY_REF}]},
    )
    return {
        AgentRole.EVIDENCE_INVESTIGATOR: (
            (
                EvidenceBrief(
                    summary="Persisted evidence requires human review.",
                    findings=(
                        EvidenceFinding(
                            statement="A persisted rule matched.",
                            evidence_refs=(_AUTHORITY_REF,),
                        ),
                    ),
                ),
                (evidence_call,),
            ),
        ),
        AgentRole.REGULATORY_ANALYST: (
            (
                RegulatoryBrief(
                    summary="The governed provision may apply.",
                    findings=(
                        RegulatoryFinding(
                            citation_id=_CITATION,
                            title="Structuring",
                            application="The persisted pattern warrants human review.",
                        ),
                    ),
                ),
                (),
            ),
        ),
        AgentRole.SAR_WRITER: (
            (_draft(evidence_ref="rule-hit:forged:0", citation_id=_NEAR_CITATION), ()),
            (_draft(evidence_ref=_AUTHORITY_REF, citation_id=_CITATION), ()),
        ),
        AgentRole.COMPLIANCE_REVIEWER: (
            (ReviewVerdict(decision=ReviewDecision.PASS), ()),
            (ReviewVerdict(decision=ReviewDecision.PASS), ()),
        ),
    }


def _wire_app(
    app: Any,
    engine: AsyncEngine,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    app.state.db_engine = engine
    app.state.db_sessionmaker = sessionmaker
    app.state.run_manager = RunManager(
        sessionmaker=sessionmaker,
        components=app.state.pipeline_components,
        settings=app.state.settings,
    )


async def _seed_transaction(
    session: AsyncSession,
    *,
    agency_id: uuid.UUID,
    label: str,
    occurred_at: datetime,
    features: dict[str, JsonValue] | None = None,
    account: str | None = None,
) -> Transaction:
    transaction = Transaction(
        agency_id=agency_id,
        external_id=f"security-{label}",
        amount=Decimal("9500.00"),
        currency="USD",
        occurred_at=occurred_at,
        origin_account=account or f"masked-{label}",
        dest_account=f"counterparty-{label}",
        channel="wire",
        country="US",
        features=features or {},
        feature_hash=f"hash-{label}",
    )
    session.add(transaction)
    await session.flush()
    return transaction


async def _seed_tool_tenants(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    agency_a = uuid.uuid4()
    agency_b = uuid.uuid4()
    run_a = uuid.uuid4()
    run_b = uuid.uuid4()
    now = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
    async with sessionmaker() as session:
        session.add_all(
            [
                Agency(id=agency_a, name="Security A", slug=f"security-a-{agency_a.hex}"),
                Agency(id=agency_b, name="Security B", slug=f"security-b-{agency_b.hex}"),
            ]
        )
        for label, agency_id, run_id in (
            ("a", agency_a, run_a),
            ("b", agency_b, run_b),
        ):
            account = f"masked-{label}"
            current = await _seed_transaction(
                session,
                agency_id=agency_id,
                label=f"current-{label}",
                occurred_at=now,
                features={"freeText": _INJECTION},
                account=account,
            )
            await _seed_transaction(
                session,
                agency_id=agency_id,
                label=f"history-{label}",
                occurred_at=now - timedelta(hours=1),
                features={"freeText": _INJECTION},
                account=account,
            )
            session.add(
                AnalysisRun(
                    id=run_id,
                    agency_id=agency_id,
                    transaction_id=current.id,
                    status=RunStatus.COMPLETED,
                    risk_score=0.91,
                    risk_band=RiskBand.HIGH,
                )
            )
            session.add(
                AnalysisResult(
                    agency_id=agency_id,
                    run_id=run_id,
                    fraud_probability=0.91,
                    shap_values={"amount": 0.5},
                    top_features=[{"feature": "amount", "value": 9500, "shapValue": 0.5}],
                    rule_hits=[
                        {
                            "code": "STRUCT",
                            "ruleType": "structuring",
                            "severity": "high",
                            "weight": "1.0",
                        }
                    ],
                    combined_score=0.91,
                    risk_band=RiskBand.HIGH,
                    model_version="security-model",
                )
            )
            session.add(
                Alert(
                    agency_id=agency_id,
                    transaction_id=current.id,
                    run_id=run_id,
                    status=AlertStatus.OPEN,
                    severity=Severity.HIGH,
                    review_flags=[],
                )
            )
        await session.commit()
    return agency_a, agency_b, run_a, run_b


def _collection_size(tool_name: str, result: BaseModel) -> int:
    field = {
        "transaction_history": "transactions",
        "rule_hits": "hits",
        "shap_drivers": "drivers",
        "alert_history": "alerts",
        "regulation_search": "matches",
    }[tool_name]
    return len(cast(tuple[object, ...], getattr(result, field)))


def _sse_event_names(body: str) -> list[str]:
    return [
        line.removeprefix("event: ") for line in body.splitlines() if line.startswith("event: ")
    ]
