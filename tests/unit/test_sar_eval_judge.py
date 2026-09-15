"""Blind SAR evaluation judge, quote-integrity, spend, and checkpoint tests."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from sar_eval_fakes import (
    _SCORING_MODEL,
    _api_runs,
    _Judge,
    _scenarios,
)

from fraudlens_backend.settings import find_config_dir
from fraudlens_llm import LlmMessage, load_catalog
from lib.sar_eval.config import load_sar_eval_config
from lib.sar_eval.judge import (
    CandidateScore,
    ElementName,
    ElementScore,
    JudgeCheckpoint,
    JudgeClient,
    JudgePromptTemplate,
    JudgeResponse,
    JudgmentArtifact,
    _canonicalize_quote_integrity,
    load_judgments,
    model_family,
    run_judge_stage,
    write_judgments,
)


async def test_judge_stage_is_blind_deterministic_structured_three_sample_and_bounded(
    tmp_path: Path,
) -> None:
    scenarios = _scenarios()
    runs = _api_runs(scenarios)
    jittered_regulations = (
        runs.results[1]
        .facts.retrieved_regulations[0]
        .model_copy(
            update={
                "score": runs.results[1].facts.retrieved_regulations[0].score + 0.001,
            }
        ),
        *runs.results[1].facts.retrieved_regulations[1:],
    )
    jittered_facts = runs.results[1].facts.model_copy(
        update={"retrieved_regulations": jittered_regulations}
    )
    runs = runs.model_copy(
        update={
            "results": (
                runs.results[0],
                runs.results[1].model_copy(update={"facts": jittered_facts}),
                *runs.results[2:],
            )
        }
    )
    config = load_sar_eval_config()
    prompt = JudgePromptTemplate.load(config.judge.prompt_id)
    catalog = load_catalog(find_config_dir() / "llm" / "catalog.yml")
    first_client = _Judge()
    second_client = _Judge()
    checkpoint_path = tmp_path / "judgments.checkpoint.json"

    first = await run_judge_stage(
        scenarios,
        runs,
        config,
        client=cast(JudgeClient, first_client),
        catalog=catalog,
        prompt=prompt,
        max_usd=Decimal("10"),
        checkpoint_path=checkpoint_path,
    )
    second = await run_judge_stage(
        scenarios,
        runs,
        config,
        client=cast(JudgeClient, second_client),
        catalog=catalog,
        prompt=prompt,
        max_usd=Decimal("10"),
        checkpoint_path=checkpoint_path,
    )

    assert len(first.samples) == 96
    assert second == first
    assert not second_client.calls
    checkpoint = JudgeCheckpoint.model_validate_json(checkpoint_path.read_text(encoding="utf-8"))
    assert len(checkpoint.samples) == 96
    assert len(checkpoint.reservations) == 96
    assert [item.presented_order for item in first.samples] == [
        item.presented_order for item in second.samples
    ]
    assert len({item.presented_order for item in first.samples}) == 2
    assert all("response_schema" in kwargs for _messages, kwargs in first_client.calls)
    assert all(
        "single_writer" not in (message.content or "")
        and "multi_agent" not in (message.content or "")
        for messages, _kwargs in first_client.calls
        for message in cast(Sequence[LlmMessage], messages)
    )
    assert first.prompt_hash == prompt.prompt_hash
    assert first.model_family == "anthropic"
    assert first.spent_usd < first.authorized_max_usd
    evidence_message = cast(Sequence[LlmMessage], first_client.calls[0][0])[1].content or ""
    subject = scenarios.scenarios[0].transactions[-1]
    assert str(subject.amount) in evidence_message
    assert subject.channel in evidence_message
    assert subject.country in evidence_message
    assert scenarios.scenarios[0].expected_citation_ids[0] in evidence_message
    assert '"fraudProbability": 0.91' in evidence_message
    assert '"code": "structuring"' in evidence_message
    assert '"feature": "amount_log"' in evidence_message
    assert '"shapValue": 0.72' in evidence_message
    assert f'"modelVersion": "{_SCORING_MODEL}"' in evidence_message
    assert '"retrievedRegulations"' in evidence_message
    assert "No person shall structure a transaction." in evidence_message
    assert '"historicalSyntheticCount": 3' in evidence_message
    assert scenarios.scenarios[0].scenario_id in evidence_message
    assert scenarios.scenarios[1].scenario_id not in evidence_message
    assert "agentGeneratedConclusion" not in evidence_message
    assert "mustNotAppear" not in evidence_message
    target = tmp_path / first.run_id / "judgments.json"
    write_judgments(target, first)
    assert load_judgments(target) == first
    raw = first.model_dump(mode="json", by_alias=True)
    raw["spentUsd"] = "11"
    with pytest.raises(ValueError, match="spend exceeds"):
        JudgmentArtifact.model_validate(raw)

    raw = first.model_dump(mode="json", by_alias=True)
    raw["modelFamily"] = "openai"
    with pytest.raises(ValueError, match="modelFamily must match"):
        JudgmentArtifact.model_validate(raw)


def test_judge_structured_boundaries_and_provenance_reject_drift() -> None:
    with pytest.raises(ValueError, match="require a span"):
        ElementScore(element="who", present=True, quoted_span=None)
    names: tuple[ElementName, ...] = ("why", "where", "when", "what", "who")
    elements = tuple(
        ElementScore(element=element, present=True, quoted_span=element) for element in names
    )
    with pytest.raises(ValueError, match="canonical order"):
        CandidateScore(candidate="A", elements=elements)

    canonical = tuple(reversed(elements))
    candidate = CandidateScore(candidate="A", elements=canonical)
    with pytest.raises(ValueError, match="A and B"):
        JudgeResponse(candidates=(candidate, candidate))
    with pytest.raises(ValueError, match="router, family, and model"):
        model_family("missing-family")


async def test_judge_rejects_writer_family_match_and_reserves_before_call() -> None:
    scenarios = _scenarios()
    config = load_sar_eval_config()
    prompt = JudgePromptTemplate.load(config.judge.prompt_id)
    catalog = load_catalog(find_config_dir() / "llm" / "catalog.yml")
    client = _Judge()

    matched_results = tuple(
        item.model_copy(
            update={
                "writer_model_id": "openrouter/anthropic/claude-sonnet-4.6",
                "model_ids": ("openrouter/anthropic/claude-sonnet-4.6",),
            }
        )
        for item in _api_runs(scenarios).results
    )
    matched = _api_runs(scenarios).model_copy(update={"results": matched_results})
    with pytest.raises(ValueError, match="judge model family"):
        await run_judge_stage(
            scenarios,
            matched,
            config,
            client=cast(JudgeClient, client),
            catalog=catalog,
            prompt=prompt,
            max_usd=Decimal("10"),
        )
    assert not client.calls
    with pytest.raises(ValueError, match="share run and config identity"):
        await run_judge_stage(
            scenarios,
            _api_runs(scenarios).model_copy(update={"config_sha256": "0" * 64}),
            config,
            client=cast(JudgeClient, client),
            catalog=catalog,
            prompt=prompt,
            max_usd=Decimal("10"),
        )
    with pytest.raises(ValueError, match="current versioned prompt bytes"):
        await run_judge_stage(
            scenarios,
            _api_runs(scenarios),
            config,
            client=cast(JudgeClient, client),
            catalog=catalog,
            prompt=prompt.model_copy(update={"prompt_hash": "0" * 64}),
            max_usd=Decimal("10"),
        )
    runs = _api_runs(scenarios)
    drifted_facts = runs.results[1].facts.model_copy(update={"fraud_probability": 0.1})
    drifted_pair = runs.results[1].model_copy(update={"facts": drifted_facts})
    with pytest.raises(ValueError, match="identical durable evaluation facts"):
        await run_judge_stage(
            scenarios,
            runs.model_copy(update={"results": (runs.results[0], drifted_pair, *runs.results[2:])}),
            config,
            client=cast(JudgeClient, client),
            catalog=catalog,
            prompt=prompt,
            max_usd=Decimal("10"),
        )
    assert not client.calls
    with pytest.raises(RuntimeError, match="hard USD cap"):
        await run_judge_stage(
            scenarios,
            _api_runs(scenarios),
            config,
            client=cast(JudgeClient, client),
            catalog=catalog,
            prompt=prompt,
            max_usd=Decimal("0.01"),
        )
    assert not client.calls

    runs = _api_runs(scenarios)
    oversized = runs.results[0].model_copy(update={"narrative": "x" * 40_000})
    oversized_runs = runs.model_copy(update={"results": (oversized, *runs.results[1:])})
    with pytest.raises(RuntimeError, match="UTF-8 byte limit"):
        await run_judge_stage(
            scenarios,
            oversized_runs,
            config,
            client=cast(JudgeClient, client),
            catalog=catalog,
            prompt=prompt,
            max_usd=Decimal("10"),
        )
    assert not client.calls


async def test_judge_rejects_hallucinated_quote_spans() -> None:
    scenarios = _scenarios()
    config = load_sar_eval_config()
    prompt = JudgePromptTemplate.load(config.judge.prompt_id)
    catalog = load_catalog(find_config_dir() / "llm" / "catalog.yml")
    client = _Judge(hallucinated_span=True)

    with pytest.raises(RuntimeError, match="quoted span absent"):
        await run_judge_stage(
            scenarios,
            _api_runs(scenarios),
            config,
            client=cast(JudgeClient, client),
            catalog=catalog,
            prompt=prompt,
            max_usd=Decimal("10"),
        )
    assert len(client.calls) == 1


def test_judge_canonicalizes_only_unique_whitespace_quote_drift() -> None:
    elements = tuple(
        ElementScore(
            element=cast(ElementName, element),
            present=True,
            quoted_span="Synthetic   paired-candidate",
        )
        for element in ("who", "what", "when", "where", "why")
    )
    response = JudgeResponse(
        candidates=(
            CandidateScore(candidate="A", elements=elements),
            CandidateScore(candidate="B", elements=elements),
        )
    )
    narrative = "Synthetic paired-candidate narrative."

    canonical = _canonicalize_quote_integrity(response, {"A": narrative, "B": narrative})

    assert all(
        element.quoted_span == "Synthetic paired-candidate"
        for candidate in canonical.candidates
        for element in candidate.elements
    )
    ambiguous = _canonicalize_quote_integrity(
        response,
        {
            "A": f"{narrative} {narrative}",
            "B": f"{narrative} {narrative}",
        },
    )
    assert all(
        not element.present and element.quoted_span is None
        for candidate in ambiguous.candidates
        for element in candidate.elements
    )


def test_judge_canonicalizes_one_unique_majority_quote_anchor() -> None:
    elements = tuple(
        ElementScore(
            element=cast(ElementName, element),
            present=True,
            quoted_span="evidence artifacts referenced by evidence ID synthetic-123",
        )
        for element in ("who", "what", "when", "where", "why")
    )
    response = JudgeResponse(
        candidates=(
            CandidateScore(candidate="A", elements=elements),
            CandidateScore(candidate="B", elements=elements),
        )
    )
    narrative = (
        "The evidence artifacts referenced by the system were unavailable; "
        "confirm evidence ID synthetic-123."
    )

    canonical = _canonicalize_quote_integrity(response, {"A": narrative, "B": narrative})

    assert all(
        element.quoted_span == "evidence artifacts referenced by"
        for candidate in canonical.candidates
        for element in candidate.elements
    )


def test_judge_canonicalizes_typographic_punctuation_to_exact_narrative_text() -> None:
    elements = tuple(
        ElementScore(
            element=cast(ElementName, element),
            present=True,
            quoted_span="Destination country IR is on the institution's high-risk list.",
        )
        for element in ("who", "what", "when", "where", "why")
    )
    response = JudgeResponse(
        candidates=(
            CandidateScore(candidate="A", elements=elements),
            CandidateScore(candidate="B", elements=elements),
        )
    )
    narrative = (
        "Destination country IR is on the institution\u2019s high-risk list. "
        "Destination country IR is on the high-risk list."
    )

    canonical = _canonicalize_quote_integrity(response, {"A": narrative, "B": narrative})

    assert all(
        element.quoted_span == "Destination country IR is on the institution\u2019s high-risk list."
        for candidate in canonical.candidates
        for element in candidate.elements
    )


async def test_judge_checkpoint_settles_invalid_attempt_and_resumes(tmp_path: Path) -> None:
    scenarios = _scenarios()
    runs = _api_runs(scenarios)
    config = load_sar_eval_config()
    prompt = JudgePromptTemplate.load(config.judge.prompt_id)
    catalog = load_catalog(find_config_dir() / "llm" / "catalog.yml")
    checkpoint_path = tmp_path / "judgments.checkpoint.json"

    with pytest.raises(RuntimeError, match="quoted span absent"):
        await run_judge_stage(
            scenarios,
            runs,
            config,
            client=cast(JudgeClient, _Judge(hallucinated_span=True)),
            catalog=catalog,
            prompt=prompt,
            max_usd=Decimal("10"),
            checkpoint_path=checkpoint_path,
        )

    failed = JudgeCheckpoint.model_validate_json(checkpoint_path.read_text(encoding="utf-8"))
    assert len(failed.reservations) == 1
    assert failed.reservations[0].cost_usd is not None
    assert not failed.samples

    resumed = await run_judge_stage(
        scenarios,
        runs,
        config,
        client=cast(JudgeClient, _Judge()),
        catalog=catalog,
        prompt=prompt,
        max_usd=Decimal("10"),
        checkpoint_path=checkpoint_path,
    )

    final = JudgeCheckpoint.model_validate_json(checkpoint_path.read_text(encoding="utf-8"))
    first_attempts = [
        item
        for item in final.reservations
        if item.scenario_id == scenarios.scenarios[0].scenario_id and item.sample_index == 1
    ]
    assert len(resumed.samples) == 96
    assert [item.attempt for item in first_attempts] == [1, 2]
    assert resumed.spent_usd == final.observed_spend_usd
