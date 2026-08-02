"""The configured case pack must still produce the configured story through REAL scoring.

Phase 4 authored the payloads by hand against the live seeded `aml_rules.params` and the pinned
model. Without this file that calibration would only exist in a transcript: a retuned rule param
or a swapped bundle would quietly tell a different story instead of failing. So the story is
re-derived here through the same path production uses — `build_canonical` -> `PhiMasker` ->
`TransactionRepository.ingest` -> `build_pipeline_input` (masked-account history windows) ->
`RuleRegistry.evaluate` (definitions loaded from the SEEDED rows) -> `Scorer` (the pinned bundle)
-> `RiskPolicy.assess` (blend/bands loaded from the SEEDED `system_config`) — and compared with
`config/portfolio-demo.yaml`. Every expected value is READ from the config; none is typed here.

Two tiers, because the two halves have different dependencies:
  * Rule codes need only the seeded params + the authored payloads + the history windows, so they
    are asserted unconditionally and run everywhere.
  * Bands need the pinned artifact, which `.gitignore` does not track (only `v0-fixture` is
    committed), so those assertions skip with a clear reason when the bundle is absent rather than
    silently passing on a substitute model.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import AsyncIterator, Callable
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.db.repositories import RuleRepository, TransactionRepository
from fraudlens_backend.pipeline_wiring import build_pipeline_input, load_risk_policy
from fraudlens_backend.portfolio_demo import (
    PortfolioDemoConfig,
    PortfolioDemoScenario,
    load_portfolio_demo_config,
)
from fraudlens_backend.settings import AppSettings
from fraudlens_core import RuleRegistry, build_canonical
from fraudlens_ml.scoring.artifacts import DeploymentPointer, ModelCache
from fraudlens_ml.scoring.scorer import Scorer
from seed import _ensure_config, _ensure_rules  # scripts/ is on sys.path via conftest

_MODELS_DIR = Path(__file__).resolve().parents[2] / "data" / "models"
_UNSCORED = "unscored"


@pytest.fixture
def story() -> PortfolioDemoConfig:
    """Return the committed story whose expectations this module re-derives."""
    return load_portfolio_demo_config()


@pytest.fixture
def settings(make_settings: Callable[..., AppSettings]) -> AppSettings:
    """Return settings carrying the production history window/cap the pipeline reads."""
    return make_settings()


@pytest.fixture
async def ingested(
    db_sessionmaker: async_sessionmaker[AsyncSession], story: PortfolioDemoConfig
) -> AsyncIterator[tuple[AsyncSession, dict[str, uuid.UUID]]]:
    """Seed rules + runtime config, ingest every scenario, and yield the session + row ids.

    Rows are ingested oldest-first so each scenario's history already exists when it is scored,
    exactly as the bootstrap's configured order will do.
    """
    async with db_sessionmaker() as session:
        await _ensure_rules(session)
        await _ensure_config(session)
        await session.commit()

        repo = TransactionRepository(session, story.agency.id)
        ids: dict[str, uuid.UUID] = {}
        for scenario in sorted(
            story.scenarios, key=lambda item: item.transaction.occurred_offset_hours
        ):
            outcome = await repo.ingest(
                build_canonical(
                    external_id=story.external_id(scenario),
                    amount=scenario.transaction.amount,
                    currency=scenario.transaction.currency,
                    occurred_at=story.occurred_at(scenario),
                    origin_account=scenario.transaction.origin_account,
                    dest_account=scenario.transaction.dest_account,
                    channel=scenario.transaction.channel,
                    country=scenario.transaction.country,
                    features=dict(scenario.transaction.features),
                )
            )
            ids[scenario.scenario_id] = outcome.transaction.id
        await session.commit()
        yield session, ids


async def _fired_codes(
    session: AsyncSession,
    story: PortfolioDemoConfig,
    settings: AppSettings,
    scenario: PortfolioDemoScenario,
) -> tuple[tuple[str, ...], Decimal]:
    """Return the rule codes a scenario actually fires, plus its weighted subscore."""
    repo = TransactionRepository(session, story.agency.id)
    transaction = await repo.get_by_external_id(story.external_id(scenario))
    assert transaction is not None, f"{scenario.scenario_id} was not ingested"
    pipeline_input = await build_pipeline_input(
        repo=repo,
        transaction=transaction,
        run_id=uuid.uuid4(),
        agency_id=story.agency.id,
        settings=settings,
    )
    definitions = await RuleRepository(session, story.agency.id).load_definitions()
    evaluation = RuleRegistry().evaluate(definitions, pipeline_input.rule_context)
    return tuple(sorted(hit.code for hit in evaluation.hits)), evaluation.subscore


def _pinned_bundle(story: PortfolioDemoConfig) -> Path:
    """Return the pinned bundle directory, skipping the test when it is not present."""
    bundle = _MODELS_DIR / story.model.version_label
    if not (bundle / "model.json").is_file():
        pytest.skip(
            "the pinned model bundle is not tracked in git (only v0-fixture is); "
            "fetch/train it to re-verify the calibrated bands"
        )
    return bundle


async def test_every_scenario_ingests_under_its_derived_external_id(
    ingested: tuple[AsyncSession, dict[str, uuid.UUID]], story: PortfolioDemoConfig
) -> None:
    """The story's row count and derived ids are what the bootstrap will find."""
    session, ids = ingested
    assert len(ids) == story.expected.transactions
    repo = TransactionRepository(session, story.agency.id)
    for scenario in story.scenarios:
        assert await repo.get_by_external_id(story.external_id(scenario)) is not None


async def test_each_scenario_fires_exactly_its_configured_rule_codes(
    ingested: tuple[AsyncSession, dict[str, uuid.UUID]],
    story: PortfolioDemoConfig,
    settings: AppSettings,
) -> None:
    """Rule codes are the drift canary: retuned params fail here, loudly and per scenario."""
    session, _ = ingested
    deltas: list[str] = []
    for scenario in story.scored_scenarios:
        codes, _ = await _fired_codes(session, story, settings, scenario)
        expected = tuple(sorted(scenario.expected_triggered_rules))
        if codes != expected:
            deltas.append(f"{scenario.scenario_id}: expected {expected}, fired {codes}")
    assert not deltas, "configured rule codes no longer match the live rules: " + "; ".join(deltas)


async def test_the_critical_scenarios_are_corroborated_by_the_rules(
    ingested: tuple[AsyncSession, dict[str, uuid.UUID]],
    story: PortfolioDemoConfig,
    settings: AppSettings,
) -> None:
    """CRITICAL is unreachable on model score alone, so its rows must carry real rule weight.

    The requirement is DERIVED from the live policy (`critical_bound / model_weight` above 1.0
    means the model alone cannot get there), never restated as a literal.
    """
    session, _ = ingested
    from fraudlens_core import RiskBand  # noqa: PLC0415 - local to keep the module's imports flat

    policy = await load_risk_policy(session)
    critical_bound = policy.band_thresholds[RiskBand.CRITICAL]
    required_rule_share = (critical_bound - policy.model_weight) / (1.0 - policy.model_weight)
    assert required_rule_share > 0.0, "this test only means something while critical needs rules"

    criticals = [s for s in story.scored_scenarios if s.expected_risk_band is RiskBand.CRITICAL]
    assert criticals, "the story pins at least one critical row"
    for scenario in criticals:
        _, subscore = await _fired_codes(session, story, settings, scenario)
        assert float(subscore) >= required_rule_share, (
            f"{scenario.scenario_id}: rules subscore {subscore} cannot corroborate critical "
            f"(needs >= {required_rule_share:.4f})"
        )


async def test_the_held_unscored_rows_cannot_perturb_a_pinned_expectation(
    story: PortfolioDemoConfig,
) -> None:
    """Every `score: false` row occurs after every scored row, so investigating one is safe."""
    scored_latest = max(s.transaction.occurred_offset_hours for s in story.scored_scenarios)
    unscored = [s for s in story.scenarios if not s.score]
    assert len(unscored) == story.expected.unscored
    for scenario in unscored:
        assert scenario.transaction.occurred_offset_hours > scored_latest, (
            f"{scenario.scenario_id} precedes a scored row, so a live investigation could "
            "change that row's history window"
        )


async def test_real_scoring_reproduces_every_configured_band(
    ingested: tuple[AsyncSession, dict[str, uuid.UUID]],
    story: PortfolioDemoConfig,
    settings: AppSettings,
) -> None:
    """The pinned model + seeded policy must band each row exactly as the config claims."""
    session, _ = ingested
    _pinned_bundle(story)
    scorer = Scorer(ModelCache(_MODELS_DIR))
    pointer = DeploymentPointer(
        active_version_label=story.model.version_label,
        active_artifact_uri=story.model.version_label,
    )
    policy = await load_risk_policy(session)
    definitions = await RuleRepository(session, story.agency.id).load_definitions()
    repo = TransactionRepository(session, story.agency.id)

    deltas: list[str] = []
    achieved: Counter[str] = Counter()
    for scenario in story.scenarios:
        if not scenario.score:
            achieved[_UNSCORED] += 1
            continue
        transaction = await repo.get_by_external_id(story.external_id(scenario))
        assert transaction is not None
        pipeline_input = await build_pipeline_input(
            repo=repo,
            transaction=transaction,
            run_id=uuid.uuid4(),
            agency_id=story.agency.id,
            settings=settings,
        )
        evaluation = RuleRegistry().evaluate(definitions, pipeline_input.rule_context)
        score = scorer.score(pointer, pipeline_input.rule_context)
        assessment = policy.assess(
            fraud_probability=score.fraud_probability,
            rules_subscore=evaluation.subscore,
            model_thresholds=score.risk_thresholds,
        )
        achieved[assessment.risk_band.value] += 1
        if assessment.risk_band is not scenario.expected_risk_band:
            deltas.append(
                f"{scenario.scenario_id}: expected {scenario.expected_risk_band}, "
                f"scored {assessment.risk_band.value} (combined {assessment.combined_score:.4f})"
            )
        # A row alerts exactly when it was configured to carry an alert target.
        if (scenario.alert_target is not None) != assessment.alert:
            deltas.append(
                f"{scenario.scenario_id}: alert={assessment.alert} but alert_target="
                f"{scenario.alert_target}"
            )
    assert not deltas, "real scoring no longer matches the pinned story: " + "; ".join(deltas)

    expected = {band.value: count for band, count in story.expected.risk_bands.items()}
    expected[_UNSCORED] = story.expected.unscored
    assert dict(achieved) == expected


async def test_the_pinned_bundle_carries_the_configured_feature_spec(
    story: PortfolioDemoConfig,
) -> None:
    """The story is calibrated against a specific feature spec, so the artifact must match it."""
    from fraudlens_ml.scoring import load_artifact  # noqa: PLC0415 - only needed with a bundle

    bundle = _pinned_bundle(story)
    loaded = load_artifact(bundle)
    assert loaded.version_label == story.model.version_label
    assert loaded.feature_spec.version == story.model.feature_spec_version
    assert loaded.risk_thresholds is not None, (
        "the story's band arithmetic assumes the operating-point map, which needs non-null "
        "risk_thresholds on the artifact"
    )
