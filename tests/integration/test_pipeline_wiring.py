"""Tests for the backend pipeline wiring (plan §16 Phase 8): the port adapters that map the real
`Scorer`/`Explainer`/`Retriever` onto the pipeline's light ports (exercised against the committed
v0-fixture model + an empty RAG index), `build_pipeline_input` (windowed same-account history with
correct directions), `load_risk_policy` (config from `system_config` with safe defaults), and the
`build_pipeline_components` smoke. These cover the heavy→light boundary the unit/persistence tests
deliberately fake out."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tenancy import new_agency_id

import fraudlens_backend.pipeline_wiring as wiring
from fraudlens_backend.agents.config import AgentRole
from fraudlens_backend.agents.mock import MockAgentTeam
from fraudlens_backend.db.models import (
    Agency,
    AgentExecution,
    AnalysisRun,
    RunStatus,
    SystemConfig,
    Transaction,
)
from fraudlens_backend.db.repositories import TransactionRepository
from fraudlens_backend.pipeline_wiring import (
    ExplainerAdapter,
    RetrieverAdapter,
    RulesAdapter,
    ScorerAdapter,
    build_pipeline_components,
    build_pipeline_deps,
    build_pipeline_input,
    load_risk_policy,
)
from fraudlens_backend.portfolio_demo import load_portfolio_demo_config
from fraudlens_backend.settings import AppSettings
from fraudlens_core import (
    DEFAULT_RULE_DEFINITIONS,
    RiskBand,
    RiskPolicy,
    RuleContext,
    RuleRegistry,
)
from fraudlens_core.rules.base import TransactionDirection
from fraudlens_ml.pipeline import StreamMessage
from fraudlens_ml.rag import HashingEmbedder, Retriever
from fraudlens_ml.sar import SarEventType, SarInput
from fraudlens_ml.scoring import DeploymentPointer, Explainer, ModelCache, Scorer

_AGENCY_ID = new_agency_id()
_NOW = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)


def _fixture_pointer(fixture_model_dir: Path) -> DeploymentPointer:
    """Build a deployment pointer at the committed v0-fixture artifact bundle."""
    return DeploymentPointer(
        active_version_label=fixture_model_dir.name, active_artifact_uri=fixture_model_dir.name
    )


def test_rules_adapter_evaluates_default_rule_set(
    make_rule_context: Callable[..., RuleContext],
) -> None:
    adapter = RulesAdapter(RuleRegistry(), DEFAULT_RULE_DEFINITIONS)
    evaluation = adapter.evaluate(make_rule_context(amount="10000", channel="wire"))
    assert 0.0 <= float(evaluation.subscore) <= 1.0
    assert evaluation.rules_version  # a deterministic fingerprint of the rule set


def test_scorer_adapter_returns_probability_and_version(
    fixture_model_dir: Path, make_rule_context: Callable[..., RuleContext]
) -> None:
    cache = ModelCache(fixture_model_dir.parent)
    adapter = ScorerAdapter(Scorer(cache), _fixture_pointer(fixture_model_dir))
    score = adapter.score(make_rule_context(amount="9500", country="NG", channel="wire"))
    assert 0.0 <= score.fraud_probability <= 1.0
    assert score.model_version_label == fixture_model_dir.name
    assert score.was_canary is False


def test_explainer_adapter_returns_top_features(
    fixture_model_dir: Path, make_rule_context: Callable[..., RuleContext]
) -> None:
    cache = ModelCache(fixture_model_dir.parent)
    adapter = ExplainerAdapter(Explainer(), cache, _fixture_pointer(fixture_model_dir))
    shap = adapter.explain(make_rule_context(amount="9500", country="NG"))
    assert shap.top_features  # at least one driver
    assert all(feature.feature for feature in shap.top_features)
    assert shap.shap_values  # the full contribution map


def test_scorer_and_explainer_adapters_raise_without_deployment(
    make_rule_context: Callable[..., RuleContext], fixture_model_dir: Path
) -> None:
    cache = ModelCache(fixture_model_dir.parent)
    with pytest.raises(RuntimeError):
        ScorerAdapter(Scorer(cache), None).score(make_rule_context())
    with pytest.raises(RuntimeError):
        ExplainerAdapter(Explainer(), cache, None).explain(make_rule_context())


def test_retriever_adapter_degrades_to_empty_without_index(tmp_path: Path) -> None:
    retriever = Retriever(
        persist_dir=tmp_path / "missing",
        collection="fincen_bsa",
        embedder=HashingEmbedder(),
        rag_version="rag-v1",
    )
    result = RetrieverAdapter(retriever).retrieve("structuring", top_k=4)
    assert result.mode == "empty"
    assert result.citations == ()
    assert result.rag_context == ""
    assert result.rag_version == "rag-v1"


def test_build_pipeline_components_smoke(make_settings: Callable[..., AppSettings]) -> None:
    components = build_pipeline_components(make_settings(llm_mode="mock"))
    assert components.scorer is not None
    assert components.explainer is not None
    assert components.retriever is not None
    assert components.drafter is not None  # the mock drafter (no keys)


async def test_build_pipeline_deps_binds_fresh_mock_team_and_persists_attempts(
    make_settings: Callable[..., AppSettings],
    make_sar_input: Callable[..., SarInput],
    db_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real wiring binds a run-scoped mock team, tools, stable events, and persistence."""
    telemetry: list[dict[str, object]] = []
    monkeypatch.setattr(wiring, "log_llm_call", lambda **fields: telemetry.append(fields))
    settings = make_settings(llm_mode="mock", multi_agent_sar_enabled=True)
    components = build_pipeline_components(settings)
    story = load_portfolio_demo_config(settings=settings)
    scenario = next(
        item
        for item in story.scenarios
        if item.scenario_id == story.execution.mock_agent_revision_scenario
    )
    async with db_sessionmaker() as session:
        session.add(Agency(id=_AGENCY_ID, name="Agents", slug="agent-wiring"))
        transaction = Transaction(
            **_txn(external_id=story.external_id(scenario))  # type: ignore[arg-type]
        )
        session.add(transaction)
        await session.flush()
        run = AnalysisRun(
            agency_id=_AGENCY_ID,
            transaction_id=transaction.id,
            status=RunStatus.RUNNING,
            workflow_mode="multi_agent",
            graph_version=components.agent_config.graph_version,
        )
        session.add(run)
        await session.commit()

        async def emit(_message: StreamMessage) -> None:
            return None

        deps = await build_pipeline_deps(
            components=components,
            session=session,
            sessionmaker=db_sessionmaker,
            settings=settings,
            agency_id=_AGENCY_ID,
            run_id=run.id,
            transaction_id=transaction.id,
            workflow_mode="multi_agent",
            emit=emit,
        )
        assert isinstance(deps.drafter, MockAgentTeam)
        events = [
            event
            async for event in deps.drafter.draft(
                make_sar_input(
                    agency_id=str(_AGENCY_ID),
                    transaction_id=str(transaction.id),
                )
            )
        ]
        replayed_deps = await build_pipeline_deps(
            components=components,
            session=session,
            sessionmaker=db_sessionmaker,
            settings=settings,
            agency_id=_AGENCY_ID,
            run_id=run.id,
            transaction_id=transaction.id,
            workflow_mode="multi_agent",
            emit=emit,
        )
        replayed_events = [
            event
            async for event in replayed_deps.drafter.draft(
                make_sar_input(
                    agency_id=str(_AGENCY_ID),
                    transaction_id=str(transaction.id),
                )
            )
        ]
        executions = (
            (await session.execute(select(AgentExecution).where(AgentExecution.run_id == run.id)))
            .scalars()
            .all()
        )

    assert len(executions) == 6
    assert sum(event.type is SarEventType.AGENT_REVISION_REQUESTED for event in events) == 1
    assert events[-1].result is not None
    assert events[-1].result.workflow == "multi_agent"
    assert not any(event.agent is not None for event in replayed_events)
    assert len(telemetry) == 6
    assert {item["agent"] for item in telemetry} == {role.value for role in AgentRole}
    assert all(item["latency_ms"] == 0 for item in telemetry)
    assert all("attempt" in item for item in telemetry)


def _txn(**overrides: object) -> dict[str, object]:
    """A masked transaction row payload (account already masked, as stored)."""
    body: dict[str, object] = {
        "agency_id": _AGENCY_ID,
        "external_id": "x",
        "amount": Decimal("9500.00"),
        "currency": "USD",
        "occurred_at": _NOW,
        "origin_account": "****1111",
        "dest_account": "****2222",
        "channel": "wire",
        "country": "US",
        "features": {},
        "feature_hash": "h",
    }
    body.update(overrides)
    return body


async def test_build_pipeline_input_loads_windowed_same_account_history(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessionmaker() as session:
        session.add(Agency(id=_AGENCY_ID, name="W", slug="w"))
        current = Transaction(**_txn(external_id="cur", occurred_at=_NOW))  # type: ignore[arg-type]
        outbound_prior = Transaction(
            **_txn(external_id="out", occurred_at=_NOW - timedelta(hours=1))  # type: ignore[arg-type]
        )
        inbound_prior = Transaction(
            **_txn(  # type: ignore[arg-type]
                external_id="in",
                occurred_at=_NOW - timedelta(hours=2),
                origin_account="****9999",
                dest_account="****1111",
            )
        )
        unrelated = Transaction(
            **_txn(  # type: ignore[arg-type]
                external_id="other",
                occurred_at=_NOW - timedelta(hours=3),
                origin_account="****8888",
                dest_account="****7777",
            )
        )
        too_old = Transaction(
            **_txn(external_id="old", occurred_at=_NOW - timedelta(hours=300))  # type: ignore[arg-type]
        )
        session.add_all([current, outbound_prior, inbound_prior, unrelated, too_old])
        await session.commit()

        repo = TransactionRepository(session, _AGENCY_ID)
        pipeline_input = await build_pipeline_input(
            repo=repo,
            transaction=current,
            run_id=uuid.uuid4(),
            agency_id=_AGENCY_ID,
            settings=AppSettings(environment="dev"),
        )

    directions = sorted(item.direction.value for item in pipeline_input.rule_context.history)
    assert directions == [
        TransactionDirection.INBOUND.value,
        TransactionDirection.OUTBOUND.value,
    ]  # the same-account prior (origin match) is outbound; the dest match is inbound
    assert pipeline_input.rule_context.transaction.direction is TransactionDirection.OUTBOUND
    assert pipeline_input.feature_hash == "h"


async def test_load_risk_policy_reads_config_then_falls_back(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessionmaker() as session:
        session.add(Agency(id=_AGENCY_ID, name="C", slug="c"))
        session.add(
            SystemConfig(
                agency_id=None,
                key="riskBandThresholds",
                value={"low": 0.0, "medium": 0.4, "high": 0.7, "critical": 0.9},
            )
        )
        session.add(SystemConfig(agency_id=None, key="alertThreshold", value=0.5))
        session.add(SystemConfig(agency_id=None, key="riskBlendModelWeight", value=0.25))
        await session.commit()
        policy = await load_risk_policy(session)

    assert policy.alert_threshold == 0.5
    assert policy.band_thresholds[RiskBand.MEDIUM] == 0.4
    assert policy.model_weight == 0.25


async def test_risk_blend_model_weight_moves_the_blend_and_the_band(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The blend weight is now a tunable `system_config` key, not a code-only constant.

    Same subscores, two configured weights, two different bands — proof the key is actually in
    the blend rather than merely parsed. Bands come from `RiskPolicy.band_for`, never a literal.
    """
    async with db_sessionmaker() as session:
        session.add(Agency(id=_AGENCY_ID, name="C", slug="c"))
        session.add(SystemConfig(agency_id=None, key="riskBlendModelWeight", value=1.0))
        await session.commit()
        model_only = await load_risk_policy(session)

        row = (
            await session.execute(
                select(SystemConfig).where(SystemConfig.key == "riskBlendModelWeight")
            )
        ).scalar_one()
        row.value = 0.0
        await session.commit()
        rules_only = await load_risk_policy(session)

    # A high model probability with no rule corroboration.
    high_model = model_only.assess(fraud_probability=0.95, rules_subscore=0.0)
    ignored_model = rules_only.assess(fraud_probability=0.95, rules_subscore=0.0)

    assert model_only.model_weight == 1.0
    assert rules_only.model_weight == 0.0
    assert high_model.combined_score == pytest.approx(0.95)
    assert ignored_model.combined_score == pytest.approx(0.0)
    assert high_model.risk_band is model_only.band_for(high_model.combined_score)
    assert ignored_model.risk_band is rules_only.band_for(ignored_model.combined_score)
    assert high_model.risk_band is not ignored_model.risk_band


async def test_load_risk_policy_defaults_when_unset_or_malformed(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    default = RiskPolicy()
    async with db_sessionmaker() as session:
        empty = await load_risk_policy(session)  # no config rows → core defaults
        session.add(Agency(id=_AGENCY_ID, name="C", slug="c"))
        session.add(SystemConfig(agency_id=None, key="alertThreshold", value="not-a-number"))
        session.add(SystemConfig(agency_id=None, key="riskBandThresholds", value="not-a-dict"))
        session.add(SystemConfig(agency_id=None, key="riskBlendModelWeight", value="not-a-number"))
        await session.commit()
        malformed = await load_risk_policy(session)

    assert empty.alert_threshold == default.alert_threshold
    assert empty.model_weight == default.model_weight  # absent key → the core fallback
    assert malformed.alert_threshold == default.alert_threshold
    assert malformed.band_thresholds == default.band_thresholds
    assert malformed.model_weight == default.model_weight  # garbage → the core fallback
