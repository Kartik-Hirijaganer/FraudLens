"""Proof that the story's boundaries are CONFIG-DRIVEN, not merely read from config once
(plan §16 Phase 8).

Every other portfolio-demo suite asserts that the pipeline reproduces the pinned distribution
under the committed configuration. That is necessary but not sufficient: a value could be parsed
and then ignored, and the tests would still pass. These tests move a configured boundary and
assert the OUTCOME moves with it — the band a score resolves to, whether a run alerts, whether an
alert lands `open` or `pending_review`, and the rules denominator the probe prints. Nothing here
asserts a specific band or count: each test compares two configurations of the same input, so it
stays true when the story is re-pinned.

The `riskBlendModelWeight` half of this contract (present / absent / garbage, and the band moving
with the weight) lives in `test_pipeline_wiring.py` beside the other `load_risk_policy` tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from portfolio_demo_identity import DEMO_AGENCY_ID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.db.models import (
    Alert,
    AlertStatus,
    AmlRule,
    AnalysisResult,
    AnalysisRun,
    RunStatus,
    Severity,
    SystemConfig,
    Transaction,
)
from fraudlens_backend.db.repositories import (
    AnalysisRunRepository,
    ModelRegistryRepository,
    SarDraftRepository,
)
from fraudlens_backend.db.repositories.alerts import compute_review_flags
from fraudlens_backend.pipeline_wiring import PipelineRunStore, load_risk_policy
from fraudlens_backend.portfolio_demo import PortfolioDemoConfig, load_portfolio_demo_config
from fraudlens_backend.portfolio_demo.probe import _render_policy, _resolve_policy
from fraudlens_backend.settings import AppSettings
from fraudlens_core import RiskBand
from fraudlens_ml.pipeline import AlertRecord
from seed import seed  # scripts/ is on sys.path via conftest

# A confident, strongly corroborated run: high enough on both axes that BOTH configurations below
# resolve it somewhere, so the difference between them is the configuration and nothing else.
_PROBABILITY = 0.97
_RULES_SUBSCORE = Decimal("0.6")


@pytest.fixture
def story() -> PortfolioDemoConfig:
    """Return the committed story (the source of every boundary these tests move)."""
    return load_portfolio_demo_config()


@pytest.fixture
def settings(make_settings: Callable[..., AppSettings], story: PortfolioDemoConfig) -> AppSettings:
    """Return settings whose provider modes match the ones the story was calibrated against."""
    return make_settings(
        llm_mode=story.execution.llm_mode, rag_embedding_mode=story.execution.rag_embedding_mode
    )


@pytest.fixture
async def seeded(
    db_sessionmaker: async_sessionmaker[AsyncSession], settings: AppSettings
) -> AsyncIterator[AsyncSession]:
    """Yield a session over the seeded foundation (agency, personas, config, baseline rules)."""
    async with db_sessionmaker() as session:
        await seed(session, settings)
        await session.commit()
        yield session


async def _set_global_config(session: AsyncSession, key: str, value: object) -> None:
    """Upsert one global `system_config` row (the seed already wrote the policy defaults)."""
    row = (
        await session.execute(
            select(SystemConfig).where(SystemConfig.agency_id.is_(None), SystemConfig.key == key)
        )
    ).scalar_one_or_none()
    if row is None:
        session.add(SystemConfig(agency_id=None, key=key, value=value))
    else:
        row.value = value
    await session.commit()


# --------------------------------------------------------------------------------------------------
# Band bounds and the alert threshold actually decide the outcome
# --------------------------------------------------------------------------------------------------


async def test_moving_the_band_bounds_moves_the_reported_band(seeded: AsyncSession) -> None:
    """The same blended score lands in different bands under two configured bound sets."""
    await _set_global_config(
        seeded, "riskBandThresholds", {"low": 0.0, "medium": 0.1, "high": 0.2, "critical": 0.3}
    )
    permissive = await load_risk_policy(seeded)
    permissive_assessment = permissive.assess(
        fraud_probability=_PROBABILITY, rules_subscore=_RULES_SUBSCORE
    )

    await _set_global_config(
        seeded, "riskBandThresholds", {"low": 0.0, "medium": 0.97, "high": 0.98, "critical": 0.99}
    )
    strict = await load_risk_policy(seeded)
    strict_assessment = strict.assess(
        fraud_probability=_PROBABILITY, rules_subscore=_RULES_SUBSCORE
    )

    # Identical inputs, identical combined score — only the configured bounds moved.
    assert permissive_assessment.combined_score == strict_assessment.combined_score
    assert permissive_assessment.risk_band is not strict_assessment.risk_band
    assert strict_assessment.risk_band is RiskBand.LOW


async def test_moving_the_alert_threshold_moves_the_alert_decision(seeded: AsyncSession) -> None:
    """The same blended score does and does not alert under two configured thresholds."""
    await _set_global_config(seeded, "alertThreshold", 0.01)
    alerting = (await load_risk_policy(seeded)).assess(
        fraud_probability=_PROBABILITY, rules_subscore=_RULES_SUBSCORE
    )

    await _set_global_config(seeded, "alertThreshold", 0.999)
    quiet = (await load_risk_policy(seeded)).assess(
        fraud_probability=_PROBABILITY, rules_subscore=_RULES_SUBSCORE
    )

    assert alerting.combined_score == quiet.combined_score
    assert alerting.alert is True
    assert quiet.alert is False


# --------------------------------------------------------------------------------------------------
# The low-confidence review window decides where a raised alert lands
# --------------------------------------------------------------------------------------------------


async def _run_with_result(session: AsyncSession, *, fraud_probability: float) -> AnalysisRun:
    """Create a completed HIGH-band run with a persisted result, ready for an alert raise."""
    transaction = Transaction(
        agency_id=DEMO_AGENCY_ID,
        external_id=f"config-driven-{fraud_probability}",
        amount=Decimal("9000.00"),
        currency="USD",
        occurred_at=datetime.now(UTC),
        origin_account="********1111",
        dest_account="********2222",
        channel="ach",
        country="US",
        features={"dataset_source": "test-fixture"},
        feature_hash="c" * 64,
    )
    session.add(transaction)
    await session.flush()
    run = AnalysisRun(
        agency_id=DEMO_AGENCY_ID,
        transaction_id=transaction.id,
        status=RunStatus.COMPLETED,
        model_version="v-test",
    )
    session.add(run)
    await session.flush()
    session.add(
        AnalysisResult(
            agency_id=DEMO_AGENCY_ID,
            run_id=run.id,
            fraud_probability=fraud_probability,
            shap_values={},
            top_features=[],
            rule_hits=[],
            combined_score=0.7,
            risk_band=RiskBand.HIGH,  # not CRITICAL: only the confidence window may flag it
            model_version="v-test",
        )
    )
    await session.commit()
    return run


async def _raise_with_margin(
    session: AsyncSession, run: AnalysisRun, *, margin: float
) -> AlertStatus:
    """Raise the pipeline alert for `run` under one review window and return its status."""
    store = PipelineRunStore(
        session=session,
        run_id=run.id,
        transaction_id=run.transaction_id,
        analysis=AnalysisRunRepository(session, DEMO_AGENCY_ID),
        registry=ModelRegistryRepository(session),
        sar=SarDraftRepository(session, DEMO_AGENCY_ID),
        review_low_confidence_margin=margin,
    )
    await store.raise_alert(AlertRecord(severity=Severity.HIGH.value, risk_band=RiskBand.HIGH))
    alert = (
        await session.execute(select(Alert).where(Alert.run_id == run.id))
    ).scalar_one_or_none()
    assert alert is not None
    return alert.status


async def test_the_review_window_flips_the_same_alert_open_or_pending(
    seeded: AsyncSession,
) -> None:
    """One probability, two configured windows: the alert lands `open` or `pending_review`.

    The probability sits just off the 0.5 decision boundary, so a narrow window leaves the run
    confident (`open`) and a wide one trips `low_model_confidence` (`pending_review`). Nothing
    else about the run changes.
    """
    probability = 0.55
    offset = abs(probability - 0.5)
    confident = await _run_with_result(seeded, fraud_probability=probability)
    uncertain = await _run_with_result(seeded, fraud_probability=probability + 1e-9)

    narrow = await _raise_with_margin(seeded, confident, margin=offset / 2)
    wide = await _raise_with_margin(seeded, uncertain, margin=offset * 2)

    assert narrow is AlertStatus.OPEN
    assert wide is AlertStatus.PENDING_REVIEW
    assert compute_review_flags(
        risk_band=RiskBand.HIGH,
        fraud_probability=probability,
        sar_status=None,
        low_confidence_margin=offset * 2,
    ) != compute_review_flags(
        risk_band=RiskBand.HIGH,
        fraud_probability=probability,
        sar_status=None,
        low_confidence_margin=offset / 2,
    )


# --------------------------------------------------------------------------------------------------
# The probe's rules denominator is summed from the ENABLED rules, not restated
# --------------------------------------------------------------------------------------------------


async def test_disabling_a_rule_changes_the_probes_printed_denominator(
    seeded: AsyncSession, story: PortfolioDemoConfig, settings: AppSettings
) -> None:
    """Disable one seeded `aml_rules` row; the probe header's `r` denominator drops by its weight.

    The denominator is what every rules subscore is divided by, so a stale one would silently
    rescale every band. Asserting on the RENDERED header proves the operator sees the change.
    """
    before = await _resolve_policy(seeded, story, settings)
    assert before.enabled_rule_codes, "the seed must install a baseline rule set"

    victim_code = before.enabled_rule_codes[0]
    victim = (
        await seeded.execute(
            select(AmlRule).where(
                AmlRule.agency_id.is_(None), AmlRule.code == victim_code, AmlRule.enabled.is_(True)
            )
        )
    ).scalar_one()
    victim_weight = victim.weight
    victim.enabled = False
    await seeded.commit()

    after = await _resolve_policy(seeded, story, settings)

    assert after.rules_denominator == before.rules_denominator - victim_weight
    assert victim_code not in after.enabled_rule_codes
    rendered_before = "\n".join(_render_policy(before))
    rendered_after = "\n".join(_render_policy(after))
    assert str(before.rules_denominator) in rendered_before
    assert str(after.rules_denominator) in rendered_after
    assert rendered_before != rendered_after
