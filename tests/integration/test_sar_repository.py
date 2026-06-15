"""Integration tests for the agency-scoped SAR draft repository (plan §9.1, §16 Phase 7)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models.enums import SarStatus
from fraudlens_backend.db.repositories import SarDraftRepository
from fraudlens_ml.sar import (
    SarCitation,
    SarDraftContent,
    SarDraftResult,
    SarDraftStatus,
    SarSection,
    SarTokenUsage,
)


def _draft_result() -> SarDraftResult:
    content = SarDraftContent(
        subject="Suspected structuring",
        narrative="Narrative.",
        sections=(SarSection(heading="Summary", body="b"),),
        cited_regulations=("31 CFR 1010.314",),
        recommended_action="Escalate",
    )
    return SarDraftResult(
        status=SarDraftStatus.DRAFT,
        content="# SAR (masked)",
        structured=content,
        citations=(
            SarCitation(
                citation="31 CFR 1010.314", title="Structuring", source="FinCEN", snippet="s"
            ),
        ),
        model_id="mock",
        prompt_version="v1@1.0.0",
        prompt_hash="hash",
        token_usage=SarTokenUsage(output_tokens=10, total_tokens=10),
        cost_usd=Decimal("0.000200"),
    )


@pytest.mark.asyncio
async def test_create_persists_camelcase_and_bumps_version(db_session: AsyncSession) -> None:
    agency_id, run_id = uuid.uuid4(), uuid.uuid4()
    repo = SarDraftRepository(db_session, agency_id)

    first = await repo.create_from_result(run_id=run_id, result=_draft_result())
    second = await repo.create_from_result(run_id=run_id, result=_draft_result())

    assert (first.version, second.version) == (1, 2)
    assert first.agency_id == agency_id
    assert first.status is SarStatus.DRAFT
    assert "citedRegulations" in first.structured  # stored camelCase
    assert first.citations[0]["citation"] == "31 CFR 1010.314"
    assert first.cost_usd == Decimal("0.000200")


@pytest.mark.asyncio
async def test_failed_result_persists_with_empty_structured(db_session: AsyncSession) -> None:
    agency_id, run_id = uuid.uuid4(), uuid.uuid4()
    repo = SarDraftRepository(db_session, agency_id)
    failed = SarDraftResult(
        status=SarDraftStatus.FAILED,
        model_id="primary/chat",
        prompt_version="v1@1.0.0",
        prompt_hash="hash",
        error_code="llm_timeout",
    )

    row = await repo.create_from_result(run_id=run_id, result=failed)

    assert row.status is SarStatus.FAILED
    assert row.structured == {}
    assert row.citations == []


@pytest.mark.asyncio
async def test_get_for_run_returns_latest_and_lists_for_alert(db_session: AsyncSession) -> None:
    agency_id, run_id, alert_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    repo = SarDraftRepository(db_session, agency_id)
    await repo.create_from_result(run_id=run_id, result=_draft_result(), alert_id=alert_id)
    await repo.create_from_result(run_id=run_id, result=_draft_result(), alert_id=alert_id)

    latest = await repo.get_for_run(run_id)
    assert latest is not None
    assert latest.version == 2
    assert len(await repo.list_for_alert(alert_id)) == 2
    assert await repo.get_for_run(uuid.uuid4()) is None  # unknown run → None


@pytest.mark.asyncio
async def test_cross_tenant_drafts_are_invisible(db_session: AsyncSession) -> None:
    owner, other, run_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await SarDraftRepository(db_session, owner).create_from_result(
        run_id=run_id, result=_draft_result()
    )
    assert await SarDraftRepository(db_session, other).get_for_run(run_id) is None
