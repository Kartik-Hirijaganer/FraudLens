"""Worker-mode pipeline stage artifact/event transaction-boundary tests."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.repositories import (
    AnalysisRunRepository,
    ModelRegistryRepository,
    SarDraftRepository,
)
from fraudlens_backend.pipeline_runs import PipelineRunStore
from fraudlens_core import RiskBand
from fraudlens_ml.pipeline import InferenceRecord, PipelineEventType, RagRecord, ResultRecord


def _store() -> tuple[PipelineRunStore, MagicMock]:
    """Build a fenced store over async repository/session mocks."""
    session = MagicMock(spec=AsyncSession)
    analysis = MagicMock(spec=AnalysisRunRepository)
    analysis.event_by_type.return_value = None
    analysis.append_event.return_value = 1
    registry = MagicMock(spec=ModelRegistryRepository)
    registry.get_version_by_label.return_value = SimpleNamespace(id=uuid.uuid4())
    store = PipelineRunStore(
        session=session,
        run_id=uuid.uuid4(),
        transaction_id=uuid.uuid4(),
        analysis=analysis,
        registry=registry,
        sar=MagicMock(spec=SarDraftRepository),
        lease_owner="worker-a",
        fencing_token=1,
    )
    return store, session


async def test_worker_stage_artifacts_commit_with_their_completion_events() -> None:
    store, session = _store()

    await store.log_inference(
        InferenceRecord(
            model_version_label="model-v1",
            was_canary=False,
            fraud_probability=0.8,
            feature_hash="f" * 64,
        )
    )
    session.commit.assert_not_awaited()
    await store.append_event(PipelineEventType.STEP_SCORING_COMPLETED, {"modelVersion": "model-v1"})
    session.commit.assert_awaited_once()
    session.commit.reset_mock()

    await store.append_event(PipelineEventType.STEP_SHAP_COMPLETED, {"featureCount": 1})
    session.commit.assert_not_awaited()
    await store.save_result(
        ResultRecord(
            fraud_probability=0.8,
            shap_values={"amount": 0.2},
            top_features=[],
            rule_hits=[],
            combined_score=0.75,
            risk_band=RiskBand.HIGH,
            model_version="model-v1",
        )
    )
    session.commit.assert_awaited_once()
    session.commit.reset_mock()

    await store.save_rag(
        RagRecord(query="high risk wire", top_k=1, chunks=[], rag_version="rag-v1")
    )
    session.commit.assert_not_awaited()
    await store.append_event(PipelineEventType.STEP_RAG_COMPLETED, {"ragVersion": "rag-v1"})
    session.commit.assert_awaited_once()
