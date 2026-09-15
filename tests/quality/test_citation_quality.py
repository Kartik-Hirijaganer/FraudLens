"""Summary: Offline citation and required-fact quality gate over all 32 SAR scenarios.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- The keyless mock and production parse/ground path run without providers, sockets, or credentials.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from fraudlens_backend.sar.drafter_mock import MockSarDrafter
from fraudlens_backend.sar.prompt import SarPromptTemplate
from fraudlens_backend.sar.schema import parse_and_ground, render_markdown
from fraudlens_core import RiskBand, TransactionDirection
from fraudlens_ml.evaluation import citation_precision_recall, required_fact_coverage
from fraudlens_ml.rag.citations import escape_as_data
from fraudlens_ml.rag.ingest import chunk_corpus, load_corpus
from fraudlens_ml.sar import SarCitation, SarInput
from lib.quality.config import load_quality_config
from lib.sar_eval.config import DEFAULT_SAR_EVAL_CONFIG, load_sar_eval_config
from lib.sar_eval.scenarios import generate_scenarios

pytestmark = pytest.mark.quality
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _citation_map() -> dict[str, SarCitation]:
    """Build one exact digest-verifiable citation per corpus provision."""
    result: dict[str, SarCitation] = {}
    for chunk in chunk_corpus(load_corpus(_REPO_ROOT / "data" / "regulations")):
        result.setdefault(
            chunk.citation,
            SarCitation(
                citation=chunk.citation,
                title=chunk.title,
                source=chunk.source,
                snippet=escape_as_data(chunk.text),
            ),
        )
    return result


async def _mock_result(sar_input: SarInput):
    drafter = MockSarDrafter(SarPromptTemplate.load())
    events = [event async for event in drafter.draft(sar_input)]
    return events[-1].result


@pytest.mark.asyncio
async def test_all_sar_eval_scenarios_meet_citation_and_fact_thresholds() -> None:
    config = load_sar_eval_config()
    scenarios = generate_scenarios(config, DEFAULT_SAR_EVAL_CONFIG.read_bytes()).scenarios
    citations = _citation_map()
    quality = load_quality_config().sar_quality
    precision_scores: list[float] = []
    recall_scores: list[float] = []
    coverage_scores: list[float] = []

    for scenario in scenarios:
        subject = scenario.transactions[-1]
        offered = tuple(citations[item] for item in scenario.expected_citation_ids)
        sar_input = SarInput(
            agency_id="synthetic-quality-tenant",
            transaction_id=scenario.subject_external_id,
            source="synthetic-generator",
            risk_band=RiskBand.HIGH,
            fraud_probability=0.91,
            amount=subject.amount,
            currency=subject.currency,
            country=subject.country,
            channel=subject.channel,
            direction=TransactionDirection.OUTBOUND,
            occurred_at=subject.occurred_at.astimezone(UTC),
            model_version=config.calibration.model_version,
            rules_version="quality-rules-v1",
            rag_version="quality-rag-v1",
            citations=offered,
        )
        result = await _mock_result(sar_input)
        parsed, grounded = parse_and_ground(
            result.structured.model_dump_json(by_alias=True), offered
        )
        metric = citation_precision_recall(
            parsed.cited_regulations,
            tuple(item.citation for item in offered),
            scenario.expected_citation_ids,
        )
        required = (
            str(subject.amount),
            subject.currency,
            subject.channel,
            subject.country,
            "91.0%",
            RiskBand.HIGH.value,
        )
        precision_scores.append(metric.precision)
        recall_scores.append(metric.recall)
        coverage_scores.append(required_fact_coverage(required, result.content).coverage)
        assert tuple(item.citation for item in grounded) == scenario.expected_citation_ids

    assert len(precision_scores) == 32
    assert min(precision_scores) >= quality.citation_precision_min
    assert min(recall_scores) >= quality.citation_recall_min
    assert min(coverage_scores) >= quality.required_fact_coverage_min


@pytest.mark.asyncio
async def test_ci_benchmark_fixture_renders_grounded_ids_only() -> None:
    citations = _citation_map()
    offered = (citations["31 CFR 1010.314"],)
    benchmark_input = SarInput(
        agency_id="synthetic-benchmark-tenant",
        transaction_id="benchmark-case-1",
        source="synthetic-generator",
        risk_band=RiskBand.HIGH,
        fraud_probability=0.91,
        amount="9500.00",
        currency="USD",
        country="US",
        channel="wire",
        direction=TransactionDirection.OUTBOUND,
        occurred_at=datetime(2024, 6, 1, 14, 0, tzinfo=UTC),
        model_version="benchmark-fixture-model",
        rules_version="benchmark-fixture-rules",
        rag_version="benchmark-fixture-rag",
        citations=offered,
    )
    result = await _mock_result(benchmark_input)
    attempted = result.structured.model_copy(
        update={"cited_regulations": (offered[0].citation, "FABRICATED-BENCHMARK-ID")}
    )

    parsed, grounded = parse_and_ground(attempted.model_dump_json(by_alias=True), offered)
    rendered = render_markdown(parsed)

    assert tuple(item.citation for item in grounded) == (offered[0].citation,)
    assert offered[0].citation in rendered
    assert "FABRICATED-BENCHMARK-ID" not in rendered


def test_stricter_temporary_threshold_rejects_a_partial_fixture(sandbox: Path) -> None:
    config_text = (_REPO_ROOT / "config" / "quality.yaml").read_text(encoding="utf-8")
    strict_path = sandbox / "quality.yaml"
    strict_path.write_text(
        config_text.replace("citation_recall_min: 0.9", "citation_recall_min: 0.95"),
        encoding="utf-8",
    )
    threshold = load_quality_config(strict_path).sar_quality.citation_recall_min
    partial = citation_precision_recall(("reg-a",), ("reg-a", "reg-b"), ("reg-a", "reg-b"))

    with pytest.raises(AssertionError):
        assert partial.recall >= threshold
