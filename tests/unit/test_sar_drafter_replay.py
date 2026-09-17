"""Persisted SAR replay tests proving a recovered run makes no second provider call."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import cast

from pipeline_fakes import passing_quality

from fraudlens_backend.db.models import SarDraft, SarQualityStatus, SarStatus
from fraudlens_backend.sar.drafter_replay import PersistedSarDrafter, resume_drafter
from fraudlens_core import RiskBand, TransactionDirection
from fraudlens_ml.sar import SarDrafter, SarEventType, SarInput

_PASSED_VERDICT = passing_quality().model_dump(by_alias=True, mode="json")


async def test_persisted_drafter_reconstructs_one_cached_terminal_result() -> None:
    draft = SarDraft(
        agency_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        version=1,
        model_id="catalog-model",
        prompt_version="prompt-v1",
        prompt_hash="p" * 64,
        workflow="single_writer",
        revision_count=0,
        content="Masked narrative.",
        structured={
            "subject": "Suspicious transfer",
            "narrative": "Masked narrative.",
            "claims": [],
            "sections": [],
            "citedRegulations": [],
            "recommendedAction": "Review the transaction.",
        },
        citations=[],
        quality_status=SarQualityStatus.PASSED,
        quality=_PASSED_VERDICT,
        status=SarStatus.DRAFT,
        token_usage={"inputTokens": 10, "outputTokens": 20, "totalTokens": 30},
        cost_usd=Decimal("0.01"),
    )
    sar_input = SarInput(
        agency_id=str(draft.agency_id),
        transaction_id=str(uuid.uuid4()),
        source="synthetic-generator",
        risk_band=RiskBand.HIGH,
        fraud_probability=0.9,
        amount=Decimal("9500"),
        currency="USD",
        country="US",
        channel="wire",
        direction=TransactionDirection.OUTBOUND,
        occurred_at="2026-09-14T00:00:00Z",
        model_version="model-v1",
        rules_version="rules-v1",
        rag_version="rag-v1",
    )

    events = [event async for event in PersistedSarDrafter(draft).draft(sar_input)]

    assert len(events) == 1 and events[0].type is SarEventType.COMPLETED
    result = events[0].result
    assert result is not None and result.cached
    assert result.content == draft.content and result.cost_usd == draft.cost_usd
    # The persisted verdict replays with the draft: a resumed run stays re-persistable.
    assert result.quality is not None and result.quality.passed is True


def test_resume_selection_replays_only_successful_machine_draft() -> None:
    primary = cast(SarDrafter, object())
    failed = SarDraft(
        agency_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        version=1,
        model_id="catalog-model",
        prompt_version="prompt-v1",
        prompt_hash="p" * 64,
        workflow="single_writer",
        content="",
        structured={},
        citations=[],
        quality_status=SarQualityStatus.FAILED,
        quality={},
        status=SarStatus.FAILED,
        token_usage={},
        cost_usd=Decimal("0"),
    )

    assert resume_drafter(primary, None) is primary
    assert resume_drafter(primary, failed) is primary
