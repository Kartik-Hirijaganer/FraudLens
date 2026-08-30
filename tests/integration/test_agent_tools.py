"""Integration tests for the read-only, tenant-scoped SAR agent capability registry."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.agents.tools import EvidenceToolset
from fraudlens_backend.db.models import (
    Agency,
    Alert,
    AlertStatus,
    AnalysisResult,
    AnalysisRun,
    Base,
    RunStatus,
    Severity,
    Transaction,
)
from fraudlens_backend.pipeline_wiring import RetrieverAdapter
from fraudlens_core import RiskBand
from fraudlens_ml.rag import RetrievalResult, RetrievedChunk, Retriever

_DB_TOOL_NAMES = (
    "transaction_history",
    "rule_hits",
    "shap_drivers",
    "alert_history",
)
_TOOL_ARGUMENTS: dict[str, dict[str, object]] = {
    "transaction_history": {},
    "rule_hits": {},
    "shap_drivers": {},
    "alert_history": {},
    "regulation_search": {"query": "structured transaction pattern", "topK": 2},
}
_PRIVATE_TRANSACTION_TEXT = "account-a-private"
_PRIVATE_CORPUS_TEXT = "corpus narrative must not leave the adapter"


class _FakeRetriever:
    """Deterministic retriever fake consumed through the real backend adapter."""

    def __init__(self) -> None:
        """Initialize call capture for delegation assertions."""
        self.calls: list[tuple[str, int]] = []

    def retrieve(self, query: str, *, top_k: int = 4) -> RetrievalResult:
        """Return one governed synthetic corpus chunk."""
        self.calls.append((query, top_k))
        return RetrievalResult(
            chunks=[
                RetrievedChunk(
                    chunk_id="reg-doc-a::0",
                    doc_id="reg-doc-a",
                    citation="31 CFR 1010.314",
                    title="Structured transactions",
                    source="FinCEN",
                    text=_PRIVATE_CORPUS_TEXT,
                    score=0.91,
                )
            ],
            mode="vector",
            rag_version="rag-test",
        )


class _TrackingSessionmaker:
    """Callable wrapper recording each independently-created async session."""

    def __init__(self, delegate: async_sessionmaker[AsyncSession]) -> None:
        """Bind the real sessionmaker and initialize the instance log."""
        self._delegate = delegate
        self.sessions: list[AsyncSession] = []

    def __call__(self) -> AsyncSession:
        """Create and record one fresh session."""
        session = self._delegate()
        self.sessions.append(session)
        return session


def _adapter(fake: _FakeRetriever | None = None) -> RetrieverAdapter:
    """Build the existing backend adapter around a structural retriever fake."""
    return RetrieverAdapter(cast(Retriever, fake or _FakeRetriever()))


async def _seed_two_agencies(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID, dict[str, uuid.UUID]]:
    """Seed equivalent investigation artifacts for two isolated agencies."""
    agency_a_id = uuid.uuid4()
    agency_b_id = uuid.uuid4()
    run_a_id = uuid.uuid4()
    run_b_id = uuid.uuid4()
    now = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
    ids: dict[str, uuid.UUID] = {}
    async with sessionmaker() as session:
        session.add_all(
            [
                Agency(id=agency_a_id, name="Agency A", slug=f"agency-a-{agency_a_id.hex}"),
                Agency(id=agency_b_id, name="Agency B", slug=f"agency-b-{agency_b_id.hex}"),
            ]
        )
        for label, agency_id, run_id in (
            ("a", agency_a_id, run_a_id),
            ("b", agency_b_id, run_b_id),
        ):
            current_id = uuid.uuid4()
            history_id = uuid.uuid4()
            alert_id = uuid.uuid4()
            ids[f"{label}_transaction"] = current_id
            ids[f"{label}_history"] = history_id
            ids[f"{label}_alert"] = alert_id
            origin_account = _PRIVATE_TRANSACTION_TEXT if label == "a" else "account-b-private"
            session.add_all(
                [
                    Transaction(
                        id=current_id,
                        agency_id=agency_id,
                        external_id=f"current-{label}",
                        amount=Decimal("9500.00"),
                        currency="USD",
                        occurred_at=now,
                        origin_account=origin_account,
                        dest_account=f"counterparty-{label}-private",
                        channel="wire",
                        country="US",
                        features={"privateNote": f"private-feature-{label}"},
                        feature_hash=f"feature-hash-{label}",
                    ),
                    Transaction(
                        id=history_id,
                        agency_id=agency_id,
                        external_id=f"history-{label}",
                        amount=Decimal("4200.00" if label == "a" else "7300.00"),
                        currency="USD",
                        occurred_at=now - timedelta(hours=2),
                        origin_account=origin_account,
                        dest_account=f"history-counterparty-{label}-private",
                        channel="ach",
                        country="US",
                        features={"privateNote": f"history-feature-{label}"},
                        feature_hash=f"history-hash-{label}",
                    ),
                    AnalysisRun(
                        id=run_id,
                        agency_id=agency_id,
                        transaction_id=current_id,
                        status=RunStatus.COMPLETED,
                        risk_score=0.91,
                        risk_band=RiskBand.HIGH,
                    ),
                    AnalysisResult(
                        agency_id=agency_id,
                        run_id=run_id,
                        fraud_probability=0.91 if label == "a" else 0.73,
                        shap_values={f"{label}_feature": 0.42},
                        top_features=[
                            {
                                "feature": f"{label}_feature",
                                "value": 4.2,
                                "shapValue": 0.42,
                                "privateNarrative": f"private-driver-{label}",
                            }
                        ],
                        rule_hits=[
                            {
                                "code": f"{label.upper()}_RULE",
                                "ruleType": "structuring",
                                "severity": "high",
                                "weight": "1.0",
                                "reason": f"private-rule-reason-{label}",
                            }
                        ],
                        combined_score=0.91 if label == "a" else 0.73,
                        risk_band=RiskBand.HIGH,
                        model_version=f"model-{label}",
                    ),
                    Alert(
                        id=alert_id,
                        agency_id=agency_id,
                        transaction_id=current_id,
                        run_id=run_id,
                        status=AlertStatus.OPEN,
                        severity=Severity.HIGH,
                        review_flags=[],
                    ),
                ]
            )
        await session.commit()
    return agency_a_id, agency_b_id, run_a_id, run_b_id, ids


def _collection_size(tool_name: str, result: BaseModel) -> int:
    """Return the evidence-record count for any approved tool result."""
    field_name = {
        "transaction_history": "transactions",
        "rule_hits": "hits",
        "shap_drivers": "drivers",
        "alert_history": "alerts",
        "regulation_search": "matches",
    }[tool_name]
    return len(cast(tuple[object, ...], getattr(result, field_name)))


async def test_registry_matches_parametrized_isolation_matrix_and_hides_tenant_argument(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    toolset = EvidenceToolset(
        db_sessionmaker,
        uuid.uuid4(),
        uuid.uuid4(),
        retriever=_adapter(),
    )

    assert set(toolset.registry) == set(_TOOL_ARGUMENTS)
    assert set(toolset.definitions) == set(_TOOL_ARGUMENTS)
    for name, spec in toolset.registry.items():
        assert spec.name == name
        schema = json.dumps(spec.parameters).lower()
        assert "agency_id" not in schema
        assert "agencyid" not in schema
        assert spec.definition() == toolset.definitions[name]


@pytest.mark.parametrize("tool_name", tuple(_TOOL_ARGUMENTS))
async def test_registry_tool_returns_only_scoped_structured_evidence(
    db_sessionmaker: async_sessionmaker[AsyncSession],
    tool_name: str,
) -> None:
    agency_a_id, _agency_b_id, run_a_id, _run_b_id, ids = await _seed_two_agencies(db_sessionmaker)
    fake = _FakeRetriever()
    toolset = EvidenceToolset(
        db_sessionmaker,
        agency_a_id,
        run_a_id,
        retriever=_adapter(fake),
    )

    result = await toolset.execute(tool_name, cast(dict[str, Any], _TOOL_ARGUMENTS[tool_name]))
    payload = result.model_dump_json(by_alias=True)

    assert _collection_size(tool_name, result) == 1
    assert str(ids["b_transaction"]) not in payload
    assert str(ids["b_history"]) not in payload
    assert str(ids["b_alert"]) not in payload
    assert "B_RULE" not in payload
    assert "b_feature" not in payload
    assert "private-feature" not in payload
    assert "private-rule-reason" not in payload
    assert _PRIVATE_TRANSACTION_TEXT not in payload
    assert _PRIVATE_CORPUS_TEXT not in payload
    if tool_name == "regulation_search":
        assert fake.calls == [("structured transaction pattern", 2)]


@pytest.mark.parametrize("tool_name", _DB_TOOL_NAMES)
async def test_cross_agency_bound_run_returns_empty_without_existence_leak(
    db_sessionmaker: async_sessionmaker[AsyncSession],
    tool_name: str,
) -> None:
    agency_a_id, _agency_b_id, _run_a_id, run_b_id, ids = await _seed_two_agencies(db_sessionmaker)
    toolset = EvidenceToolset(
        db_sessionmaker,
        agency_a_id,
        run_b_id,
        retriever=_adapter(),
    )

    result = await toolset.execute(tool_name, {})
    payload = result.model_dump_json(by_alias=True)

    assert _collection_size(tool_name, result) == 0
    assert str(run_b_id) not in payload
    assert str(ids["b_transaction"]) not in payload


async def test_each_database_capability_uses_a_distinct_session(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    agency_a_id, _agency_b_id, run_a_id, _run_b_id, _ids = await _seed_two_agencies(db_sessionmaker)
    tracking = _TrackingSessionmaker(db_sessionmaker)
    toolset = EvidenceToolset(
        cast(async_sessionmaker[AsyncSession], tracking),
        agency_a_id,
        run_a_id,
        retriever=_adapter(),
    )

    for tool_name in _DB_TOOL_NAMES:
        await toolset.execute(tool_name, {})

    assert len(tracking.sessions) == len(_DB_TOOL_NAMES)
    assert len({id(session) for session in tracking.sessions}) == len(_DB_TOOL_NAMES)


async def test_registry_is_read_only_and_rejects_unapproved_or_malformed_arguments(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    agency_a_id, _agency_b_id, run_a_id, _run_b_id, _ids = await _seed_two_agencies(db_sessionmaker)
    toolset = EvidenceToolset(
        db_sessionmaker,
        agency_a_id,
        run_a_id,
        retriever=_adapter(),
    )
    async with db_sessionmaker() as session:
        before = {
            table.name: int(
                (await session.execute(select(func.count()).select_from(table))).scalar_one()
            )
            for table in Base.metadata.sorted_tables
        }

    for tool_name, arguments in _TOOL_ARGUMENTS.items():
        await toolset.execute(tool_name, cast(dict[str, Any], arguments))

    async with db_sessionmaker() as session:
        after = {
            table.name: int(
                (await session.execute(select(func.count()).select_from(table))).scalar_one()
            )
            for table in Base.metadata.sorted_tables
        }
    assert after == before
    with pytest.raises(ValueError, match="Unknown evidence capability"):
        await toolset.execute("write_record", {})
    with pytest.raises(ValueError, match="extra_forbidden"):
        await toolset.execute("rule_hits", {"agencyId": str(agency_a_id)})
    with pytest.raises(ValueError, match="History bounds"):
        EvidenceToolset(
            db_sessionmaker,
            agency_a_id,
            run_a_id,
            retriever=_adapter(),
            history_limit=0,
        )
