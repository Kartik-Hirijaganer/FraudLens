"""The portfolio-demo bootstrap must produce the CONFIGURED story through the real pipeline, and do
it idempotently (plan §16 Phase 6).

Two tiers, for the same reason `test_portfolio_demo_calibration.py` has two:
  * the guards, operational-state detection, content-drift detection, the model-state matrix, the
    story's `job_executions` upsert, and `--reset` need no model bundle, so they run everywhere;
  * a full `apply_story` needs the pinned artifact, which `.gitignore` does not track, so those
    tests skip with a clear reason rather than silently proving the story on a substitute model.

The end-to-end tier fakes ONLY the retriever and the SAR drafter (RAG has no chroma index in tests
and the drafter is keyless-mock by config anyway). Rules, the scorer, the explainer, the run store,
and the risk policy are all real — they are what decides bands, alerts, and SAR drafts, so faking
them would prove nothing about the pinned distribution.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import AsyncIterator, Callable
from decimal import Decimal
from pathlib import Path

import pytest
from pipeline_fakes import FakeRetrieverPort, FakeSarDrafter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import fraudlens_backend.jobs.runner as batch
from fraudlens_backend.db.models import (
    Agency,
    Alert,
    AlertAction,
    AmlRule,
    AnalysisRun,
    JobExecution,
    ModelDeployment,
    ModelVersion,
    SarDraft,
    Transaction,
    User,
)
from fraudlens_backend.db.repositories import AuditLogRepository
from fraudlens_backend.portfolio_demo import PortfolioDemoConfig, load_portfolio_demo_config
from fraudlens_backend.portfolio_demo.bootstrap import (
    BootstrapRefusedError,
    OperationalState,
    apply_story,
    assert_configured_tenant,
    assert_enabled_in_prod,
    assert_execution_modes,
    detect_operational_state,
    ensure_active_model,
    reset_story,
    story_job_id,
    verify_model_bundle,
)
from fraudlens_backend.portfolio_demo.ingest import StoryIngestError, ensure_story_transactions
from fraudlens_backend.portfolio_demo.probe import probe_story, render_probe_report
from fraudlens_backend.portfolio_demo.verification import (
    count_configured_actions,
    verify_story,
)
from fraudlens_backend.settings import AppSettings
from fraudlens_ml.pipeline import PipelineDeps
from seed import seed  # scripts/ is on sys.path via conftest

_MODELS_DIR = Path(__file__).resolve().parents[2] / "data" / "models"


@pytest.fixture
def story() -> PortfolioDemoConfig:
    """Return the committed story the bootstrap must reproduce."""
    return load_portfolio_demo_config()


@pytest.fixture
def settings(make_settings: Callable[..., AppSettings], story: PortfolioDemoConfig) -> AppSettings:
    """Return settings whose provider modes match the ones the story was calibrated against."""
    return make_settings(
        llm_mode=story.execution.llm_mode,
        rag_embedding_mode=story.execution.rag_embedding_mode,
        model_artifacts_dir=str(_MODELS_DIR),
    )


@pytest.fixture
async def seeded(
    db_sessionmaker: async_sessionmaker[AsyncSession], settings: AppSettings
) -> AsyncIterator[AsyncSession]:
    """Yield a session over a database holding the seeded foundation (agency, personas, rules)."""
    async with db_sessionmaker() as session:
        await seed(session, settings)
        await session.commit()
        yield session


def _audit(story: PortfolioDemoConfig, session: AsyncSession) -> AuditLogRepository:
    """Build the story-correlated audit writer the bootstrap uses."""
    return AuditLogRepository(session, agency_id=story.agency.id, request_id=story.audit_request_id)


def _pinned_bundle(story: PortfolioDemoConfig) -> Path:
    """Return the pinned bundle dir, skipping when the untracked artifact is not present."""
    if not (_MODELS_DIR / story.model.version_label / "model.json").is_file():
        pytest.skip(
            "the pinned model bundle is not tracked in git (only v0-fixture is); "
            "train/fetch it to exercise the full bootstrap"
        )
    return _MODELS_DIR / story.model.version_label


async def _fake_promoter(session: AsyncSession, *, version_label: str) -> str:
    """Register the configured label and flip the pointer, standing in for activate_model."""
    version = (
        await session.execute(
            select(ModelVersion).where(ModelVersion.version_label == version_label)
        )
    ).scalar_one_or_none()
    if version is None:
        fixture = (await session.execute(select(ModelVersion).limit(1))).scalar_one()
        version = ModelVersion(
            version_label=version_label,
            training_run_id=fixture.training_run_id,
            artifact_uri=version_label,
            feature_spec=fixture.feature_spec,
            metrics=fixture.metrics,
            status=fixture.status,
        )
        session.add(version)
        await session.flush()
    deployment = (await session.execute(select(ModelDeployment).limit(1))).scalar_one_or_none()
    if deployment is None:
        session.add(ModelDeployment(active_version_id=version.id, canary_percent=0))
    else:
        deployment.previous_active_version_id = deployment.active_version_id
        deployment.active_version_id = version.id
    await session.flush()
    return f"activated '{version_label}'"


def _patch_real_core_fake_rag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep rules/scorer/explainer/store real; fake only RAG retrieval and SAR drafting."""
    original = batch.build_pipeline_deps

    async def _build(**kwargs: object) -> PipelineDeps:
        deps = await original(**kwargs)  # type: ignore[arg-type]
        return dataclasses.replace(deps, retriever=FakeRetrieverPort(), drafter=FakeSarDrafter())

    monkeypatch.setattr(batch, "build_pipeline_deps", _build)


# --------------------------------------------------------------------------------------------------
# Guards — every one runs before any write
# --------------------------------------------------------------------------------------------------


async def test_missing_agency_is_refused(
    db_sessionmaker: async_sessionmaker[AsyncSession], story: PortfolioDemoConfig
) -> None:
    async with db_sessionmaker() as session:
        with pytest.raises(BootstrapRefusedError, match="does not exist"):
            await assert_configured_tenant(session, story)


async def test_a_second_persistent_agency_is_refused(
    seeded: AsyncSession, story: PortfolioDemoConfig
) -> None:
    seeded.add(Agency(id=uuid.uuid4(), name="Other", slug="other-agency"))
    await seeded.commit()
    with pytest.raises(BootstrapRefusedError, match="other agency"):
        await assert_configured_tenant(seeded, story)


async def test_a_renamed_agency_is_refused(
    seeded: AsyncSession, story: PortfolioDemoConfig
) -> None:
    agency = await seeded.get(Agency, story.agency.id)
    assert agency is not None
    agency.slug = f"{agency.slug}-moved"
    await seeded.commit()
    with pytest.raises(BootstrapRefusedError, match="name/slug"):
        await assert_configured_tenant(seeded, story)


def test_prod_without_the_flag_is_refused(make_settings: Callable[..., AppSettings]) -> None:
    with pytest.raises(BootstrapRefusedError, match="disabled"):
        assert_enabled_in_prod(make_settings(environment="prod", portfolio_demo_enabled=False))
    # The same environment with the gate explicitly on is allowed.
    assert_enabled_in_prod(make_settings(environment="prod", portfolio_demo_enabled=True))


def test_mismatched_execution_modes_are_refused(
    make_settings: Callable[..., AppSettings], story: PortfolioDemoConfig
) -> None:
    with pytest.raises(BootstrapRefusedError, match="llm_mode"):
        assert_execution_modes(story, make_settings(llm_mode="live"))


def test_a_bundle_without_its_manifest_sidecar_is_refused(
    story: PortfolioDemoConfig, tmp_path: Path
) -> None:
    (tmp_path / story.model.version_label).mkdir()
    with pytest.raises(BootstrapRefusedError, match=r"metadata\.json"):
        verify_model_bundle(story, tmp_path)


def test_the_pinned_bundle_passes_every_bundle_check(story: PortfolioDemoConfig) -> None:
    _pinned_bundle(story)
    verify_model_bundle(story, _MODELS_DIR)  # label, sidecars, feature spec, booster checksum


# --------------------------------------------------------------------------------------------------
# Operational-state detection + content-drift detection
# --------------------------------------------------------------------------------------------------


async def test_an_empty_tenant_is_detected_as_empty(
    seeded: AsyncSession, story: PortfolioDemoConfig
) -> None:
    assert await detect_operational_state(seeded, story) is OperationalState.EMPTY


async def test_only_configured_rows_are_detected_as_the_story(
    seeded: AsyncSession, story: PortfolioDemoConfig
) -> None:
    await ensure_story_transactions(seeded, story)
    await seeded.commit()
    assert await detect_operational_state(seeded, story) is OperationalState.STORY


async def test_a_partial_story_still_resumes(
    seeded: AsyncSession, story: PortfolioDemoConfig
) -> None:
    # Partial recovery: a subset of the configured ids is still resumable, never "foreign".
    report = await ensure_story_transactions(seeded, story)
    first = story.scenarios[0]
    row = await seeded.get(Transaction, report.transaction_ids[first.scenario_id])
    assert row is not None
    await seeded.delete(row)
    await seeded.commit()
    assert await detect_operational_state(seeded, story) is OperationalState.STORY


async def test_a_visitor_created_row_is_detected_as_foreign(
    seeded: AsyncSession, story: PortfolioDemoConfig
) -> None:
    from datetime import UTC, datetime  # noqa: PLC0415

    from fraudlens_backend.db.repositories import TransactionRepository  # noqa: PLC0415
    from fraudlens_core import build_canonical  # noqa: PLC0415

    await TransactionRepository(seeded, story.agency.id).ingest(
        build_canonical(
            external_id="VISITOR-INGESTED-ROW",
            amount=Decimal("100.00"),
            currency="USD",
            occurred_at=datetime(2026, 6, 1, tzinfo=UTC),
            origin_account="DEMO-SYNTH-ACCT-0001",
            dest_account="DEMO-SYNTH-ACCT-0002",
            channel="card",
            country="US",
        )
    )
    await seeded.commit()
    assert await detect_operational_state(seeded, story) is OperationalState.FOREIGN


async def test_ingest_is_idempotent_and_resolves_every_scenario(
    seeded: AsyncSession, story: PortfolioDemoConfig
) -> None:
    first = await ensure_story_transactions(seeded, story)
    await seeded.commit()
    assert first.created == story.expected.transactions
    assert first.existing == 0
    second = await ensure_story_transactions(seeded, story)
    await seeded.commit()
    assert second.created == 0
    assert second.existing == story.expected.transactions
    assert set(second.transaction_ids) == {s.scenario_id for s in story.scenarios}


async def test_an_edited_payload_fails_as_feature_hash_drift(
    seeded: AsyncSession, story: PortfolioDemoConfig
) -> None:
    report = await ensure_story_transactions(seeded, story)
    await seeded.commit()
    target = story.scenarios[0]
    row = await seeded.get(Transaction, report.transaction_ids[target.scenario_id])
    assert row is not None
    row.feature_hash = "a" * 64  # simulate the stored row no longer matching the authored payload
    await seeded.commit()
    with pytest.raises(StoryIngestError, match=target.scenario_id):
        await ensure_story_transactions(seeded, story)


# --------------------------------------------------------------------------------------------------
# Model-state matrix
# --------------------------------------------------------------------------------------------------


async def test_the_seeded_fixture_is_replaced_by_the_configured_model(
    seeded: AsyncSession, story: PortfolioDemoConfig
) -> None:
    outcome = await ensure_active_model(
        seeded, story, promote=_fake_promoter, audit=_audit(story, seeded)
    )
    assert story.model.version_label in outcome
    # Re-running is a verify, not a second promotion.
    assert "already active" in await ensure_active_model(
        seeded, story, promote=_fake_promoter, audit=_audit(story, seeded)
    )


async def test_a_different_non_fixture_model_is_refused(
    seeded: AsyncSession, story: PortfolioDemoConfig
) -> None:
    await _fake_promoter(seeded, version_label="someone-elses-model")
    await seeded.commit()
    with pytest.raises(BootstrapRefusedError, match="different model"):
        await ensure_active_model(
            seeded, story, promote=_fake_promoter, audit=_audit(story, seeded)
        )


# --------------------------------------------------------------------------------------------------
# Verification + reset
# --------------------------------------------------------------------------------------------------


async def test_verification_fails_loudly_on_an_empty_tenant(
    seeded: AsyncSession, story: PortfolioDemoConfig
) -> None:
    report = await verify_story(seeded, story)
    assert not report.ok
    checks = {row.check for row in report.deltas}
    assert "transactions.total" in checks
    assert "model.activeVersionLabel" in checks
    assert "PASS" in report.render() or "FAIL" in report.render()


async def test_reset_clears_operational_rows_and_keeps_the_tenant(
    seeded: AsyncSession, story: PortfolioDemoConfig
) -> None:
    await ensure_story_transactions(seeded, story)
    await seeded.commit()
    deleted = await reset_story(seeded, story, _audit(story, seeded))
    await seeded.commit()
    assert deleted["transactions"] == story.expected.transactions
    assert await detect_operational_state(seeded, story) is OperationalState.EMPTY
    # The tenant itself, its personas, and its rules survive a reset.
    assert await seeded.get(Agency, story.agency.id) is not None
    users = (
        (await seeded.execute(select(User).where(User.agency_id == story.agency.id)))
        .scalars()
        .all()
    )
    assert len(users) == len(story.personas)
    assert (await seeded.execute(select(AmlRule))).scalars().all()


def test_the_configured_action_count_is_derived_from_the_story(
    story: PortfolioDemoConfig,
) -> None:
    """One action per non-no-op alert target, plus one comment per applied SAR decision."""
    transitions = sum(
        1
        for scenario in story.scenarios
        if scenario.alert_target is not None
        and scenario.alert_target.value not in {"open", "pending_review"}
    )
    decisions = sum(
        1
        for scenario in story.scenarios
        if scenario.sar_target is not None and scenario.sar_target.value in {"approved", "rejected"}
    )
    assert count_configured_actions(story) == transitions + decisions


# --------------------------------------------------------------------------------------------------
# Probe (needs the pinned bundle for real probabilities)
# --------------------------------------------------------------------------------------------------


async def test_the_probe_reports_the_resolved_policy_and_every_candidate(
    seeded: AsyncSession, story: PortfolioDemoConfig, settings: AppSettings
) -> None:
    _pinned_bundle(story)
    report = await probe_story(seeded, story, settings, models_dir=_MODELS_DIR)
    assert len(report.candidates) == len(story.scored_scenarios)
    assert report.unscored == story.expected.unscored
    assert report.policy.configured_model_label == story.model.version_label
    assert report.policy.rules_denominator > 0
    rendered = render_probe_report(report)
    for candidate in report.candidates:
        assert candidate.external_id in rendered
    assert "expected:" in rendered  # the paste-ready block an operator pins by hand
    # Probing persists the authored rows so history windows resolve — but no run and no band.
    assert not (await seeded.execute(select(AnalysisRun))).scalars().all()
    assert await detect_operational_state(seeded, story) is OperationalState.STORY


async def test_the_probe_reproduces_the_pinned_band_distribution(
    seeded: AsyncSession, story: PortfolioDemoConfig, settings: AppSettings
) -> None:
    _pinned_bundle(story)
    report = await probe_story(seeded, story, settings, models_dir=_MODELS_DIR)
    assert report.achieved_bands == story.expected.risk_bands
    assert report.alerting == sum(1 for s in story.scenarios if s.alert_target is not None)


# --------------------------------------------------------------------------------------------------
# End to end: apply, verify, re-apply, reset
# --------------------------------------------------------------------------------------------------


async def _apply(
    session: AsyncSession,
    story: PortfolioDemoConfig,
    settings: AppSettings,
    *,
    reset: bool = False,
) -> tuple[object, object]:
    """Run the bootstrap with real scoring and the fake RAG/SAR pair."""
    from fraudlens_backend.pipeline_wiring import build_pipeline_components  # noqa: PLC0415

    components = build_pipeline_components(settings)
    return await apply_story(
        session,
        story,
        settings,
        components=components,
        models_dir=_MODELS_DIR,
        promote=_fake_promoter,
        reset=reset,
    )


async def test_a_fresh_bootstrap_produces_the_configured_story(
    seeded: AsyncSession,
    story: PortfolioDemoConfig,
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pinned_bundle(story)
    _patch_real_core_fake_rag(monkeypatch)
    summary, report = await _apply(seeded, story, settings)
    assert report.ok, report.render()  # type: ignore[union-attr]
    assert summary.verified is True  # type: ignore[union-attr]
    assert summary.scored == len(story.scored_scenarios)  # type: ignore[union-attr]


async def test_re_running_changes_nothing_but_attempts(
    seeded: AsyncSession,
    story: PortfolioDemoConfig,
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pinned_bundle(story)
    _patch_real_core_fake_rag(monkeypatch)
    await _apply(seeded, story, settings)

    async def _counts() -> dict[str, int]:
        out: dict[str, int] = {}
        for model in (Transaction, AnalysisRun, Alert, AlertAction, SarDraft):
            rows = (await seeded.execute(select(model))).scalars().all()
            out[str(model.__tablename__)] = len(rows)
        return out

    before = await _counts()
    _, report = await _apply(seeded, story, settings)
    assert report.ok, report.render()  # type: ignore[union-attr]
    assert await _counts() == before  # no duplicated domain records
    job = await seeded.get(JobExecution, story_job_id(story))
    assert job is not None
    assert job.attempts == 2  # incremented, not appended (mirrors the foundation seed)


async def test_reset_then_rebuild_restores_the_configured_baseline(
    seeded: AsyncSession,
    story: PortfolioDemoConfig,
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pinned_bundle(story)
    _patch_real_core_fake_rag(monkeypatch)
    await _apply(seeded, story, settings)
    _, report = await _apply(seeded, story, settings, reset=True)
    assert report.ok, report.render()  # type: ignore[union-attr]
    final = await verify_story(seeded, story)
    assert final.ok, final.render()
