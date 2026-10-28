"""Risk-policy contracts: thresholds, findings and SAR evidence remain distinct."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pipeline_fakes import (
    FakeExplainerPort,
    FakeRetrieverPort,
    FakeRulesPort,
    FakeRunStore,
    FakeSarDrafter,
    FakeScorerPort,
    RecordingEmit,
)

from fraudlens_core import ModelRiskThresholds, RiskBand, RiskPolicy, RuleContext, RuleTransaction
from fraudlens_ml.pipeline import PipelineDeps, PipelineInput, Runner


def test_default_is_retained_and_probability_operating_points_are_weight_dependent():
    thresholds = ModelRiskThresholds(medium=0.001, high=0.01, critical=0.05)
    baseline = RiskPolicy()
    assert baseline.model_weight == 0.7 and baseline.alert_threshold == 0.6
    assert baseline.model_risk(0.01, thresholds) == pytest.approx(0.6 / 0.7)
    lower = RiskPolicy(model_weight=0.5)
    assert lower.model_risk(0.01, thresholds) == 1
    assert (
        lower.assess(fraud_probability=0.01, rules_subscore=0, model_thresholds=thresholds).alert
        is False
    )
    for probability, alerts in [(0.009, False), (0.01, True), (0.011, True)]:
        assessment = baseline.assess(
            fraud_probability=probability, rules_subscore=0, model_thresholds=thresholds
        )
        assert assessment.alert is alerts
    assert baseline.assess(fraud_probability=1, rules_subscore=0).risk_band is RiskBand.HIGH
    assert baseline.assess(fraud_probability=1, rules_subscore=1).risk_band is RiskBand.CRITICAL


@pytest.mark.parametrize(
    "weight,probability,alert", [(0.7, 0.1, False), (1.0, 0.1, False), (1.0, 0.9, True)]
)
async def test_rule_findings_are_preserved_without_creating_a_mandatory_override(
    weight, probability, alert
):
    store = FakeRunStore()

    class RecordingDrafter(FakeSarDrafter):
        def __init__(self):
            super().__init__()
            self.inputs = []

        async def draft(self, sar_input):
            self.inputs.append(sar_input)
            async for event in super().draft(sar_input):
                yield event

    drafter = RecordingDrafter()
    dependencies = PipelineDeps(
        rules=FakeRulesPort(),
        scorer=FakeScorerPort(probability=probability),
        explainer=FakeExplainerPort(),
        retriever=FakeRetrieverPort(),
        drafter=drafter,
        store=store,
        emit=RecordingEmit(),
        risk_policy=RiskPolicy(model_weight=weight),
    )
    transaction = RuleTransaction(
        amount=Decimal("9500"),
        currency="USD",
        country="US",
        channel="wire",
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    value = PipelineInput(
        agency_id="agency-a",
        run_id="run-a",
        transaction_id="transaction-a",
        source="synthetic-generator",
        rule_context=RuleContext(transaction=transaction),
        amount=transaction.amount,
        currency="USD",
        country="US",
        channel="wire",
        feature_hash="hash",
    )
    result = await Runner(dependencies).run(value)
    assert result.status == "completed"
    assert store.results[0].fraud_probability == probability
    assert store.results[0].rule_hits[0]["code"] == "structuring"
    assert len(store.alerts) == int(alert)
    if alert:
        assert drafter.inputs[0].rule_hits[0].code == "structuring"
        assert drafter.inputs[0].fraud_probability == probability
    else:
        assert drafter.inputs == []
