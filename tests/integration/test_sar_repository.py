"""Integration tests for the agency-scoped SAR draft repository (plan §9.1, §16 Phase 7)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from quality_gates import production_gate
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models.enums import SarQualityStatus, SarStatus
from fraudlens_backend.db.repositories import SarDraftRepository
from fraudlens_backend.db.repositories.sar import SarQualityGateNotSatisfiedError
from fraudlens_ml.sar import (
    SarCitation,
    SarDraftContent,
    SarDraftResult,
    SarDraftStatus,
    SarGateReason,
    SarGenerationAttempt,
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
        workflow="multi_agent",
        revision_count=1,
        token_usage=SarTokenUsage(output_tokens=10, total_tokens=10),
        cost_usd=Decimal("0.000200"),
        quality=_passing_verdict(),
        attempts=(
            SarGenerationAttempt(
                ordinal=0,
                stage="awq",
                model_id="vllm/Qwen/Qwen2.5-7B-Instruct-AWQ",
                connection="runpod-awq",
                outcome="rejected",
                quality=_rejected_verdict(),
                latency_ms=120,
                prompt_hash="hash",
                policy_hash=production_gate().policy_hash,
            ),
            SarGenerationAttempt(
                ordinal=1,
                stage="bf16",
                model_id="vllm/Qwen/Qwen2.5-7B-Instruct",
                connection="runpod-bf16",
                served_model="Qwen/Qwen2.5-7B-Instruct",
                outcome="passed",
                quality=_passing_verdict(),
                latency_ms=340,
                token_usage=SarTokenUsage(output_tokens=10, total_tokens=10),
                cost_usd=Decimal("0.000200"),
                prompt_hash="hash",
                policy_hash=production_gate().policy_hash,
            ),
        ),
    )


def _passing_verdict():
    """A verdict the repository boundary accepts for a reviewable draft."""
    gate = production_gate()
    return gate.rejected(SarGateReason.SCHEMA_INVALID).model_copy(
        update={"passed": True, "reasons": (), "fallback_required": False}
    )


def _rejected_verdict():
    """The tier-1 verdict a citation-failed AWQ attempt records."""
    return production_gate().rejected(SarGateReason.CITATION_FABRICATED)


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
    assert first.workflow == "multi_agent"
    assert first.revision_count == 1
    assert first.quality_status is SarQualityStatus.PASSED
    assert first.quality["passed"] is True


@pytest.mark.asyncio
async def test_human_edit_invalidates_quality_without_changing_review_lifecycle(
    db_session: AsyncSession,
) -> None:
    agency_id, run_id, actor_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    repo = SarDraftRepository(db_session, agency_id)
    base = await repo.create_from_result(run_id=run_id, result=_draft_result())

    edited = await repo.create_edited_version(
        base=base,
        content="Analyst-edited masked narrative.",
        created_by=actor_id,
    )

    assert edited.status is SarStatus.REVIEWED
    assert edited.quality_status is SarQualityStatus.NOT_RUN  # no gate judged the human narrative
    assert edited.quality == {}
    assert base.quality_status is SarQualityStatus.PASSED


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
    assert row.quality_status is SarQualityStatus.NOT_RUN


@pytest.mark.asyncio
async def test_a_draft_without_a_passing_verdict_is_refused(db_session: AsyncSession) -> None:
    """The gate is enforced at the boundary: convention cannot produce a reviewable SAR."""
    repo = SarDraftRepository(db_session, uuid.uuid4())
    ungated = _draft_result().model_copy(update={"quality": None})
    rejected = _draft_result().model_copy(update={"quality": _rejected_verdict()})

    for result in (ungated, rejected):
        with pytest.raises(SarQualityGateNotSatisfiedError):
            await repo.create_from_result(run_id=uuid.uuid4(), result=result)


@pytest.mark.asyncio
async def test_cascade_attempts_persist_in_order_under_the_owning_tenant(
    db_session: AsyncSession,
) -> None:
    agency_id, other, run_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    repo = SarDraftRepository(db_session, agency_id)

    draft = await repo.create_from_result(run_id=run_id, result=_draft_result())
    attempts = await repo.list_attempts(draft.id)

    assert [attempt.ordinal for attempt in attempts] == [0, 1]
    assert [attempt.stage for attempt in attempts] == ["awq", "bf16"]
    assert [attempt.connection for attempt in attempts] == ["runpod-awq", "runpod-bf16"]
    assert attempts[0].outcome == "rejected"
    assert attempts[0].reason_codes == ["citation_fabricated"]
    assert attempts[1].served_model == "Qwen/Qwen2.5-7B-Instruct"
    assert all(attempt.agency_id == agency_id for attempt in attempts)
    assert await SarDraftRepository(db_session, other).list_attempts(draft.id) == []


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


@pytest.mark.asyncio
async def test_a_foreign_tenant_can_neither_read_nor_write_another_agency_draft(
    db_session: AsyncSession,
) -> None:
    """Tenant isolation binds the WRITE path too: an edit or review needs the owning agency."""
    owner, other, run_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    owner_repo = SarDraftRepository(db_session, owner)
    draft = await owner_repo.create_from_result(run_id=run_id, result=_draft_result())
    intruder = SarDraftRepository(db_session, other)

    assert await intruder.get(draft.id) is None
    assert await intruder.list_for_alert(draft.id) == []
    assert await intruder.list_attempts(draft.id) == []

    # The foreign tenant cannot even version the draft it cannot see: its `_next_version` counts
    # only its OWN rows, so the write collides with the owner's version 1 and the database
    # refuses it. Isolation is enforced by the row constraint, not by the caller's good manners.
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await intruder.create_edited_version(base=draft, content="Rewritten.", created_by=None)

    latest = await owner_repo.get_for_run(run_id)
    assert latest is not None and latest.version == 1
    assert latest.content == "# SAR (masked)"
    assert await intruder.get_for_run(run_id) is None
