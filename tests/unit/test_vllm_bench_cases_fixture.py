"""Summary: Production-rendered SAR-eval benchmark case tests.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- The committed fixture model and regulation corpus are exercised without provider IO.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from fraudlens_ml.sar import SarInput
from lib.study import canonical_json
from lib.vllm_bench.cases_fixture import build_benchmark_case, build_fixture_cases
from lib.vllm_bench.config import load_config


def test_fixture_regeneration_is_deterministic_and_egress_approved() -> None:
    config = load_config()
    first = build_fixture_cases(config, profile="smoke", repo_root=Path.cwd())
    second = build_fixture_cases(config, profile="smoke", repo_root=Path.cwd())

    assert canonical_json(first) == canonical_json(second)
    assert sum(case.case_set == "measured" for case in first.cases) == 8
    assert sum(case.case_set == "warmup" for case in first.cases) == 1
    assert sum(case.case_set == "abstention" for case in first.cases) == 2
    assert all(case.data_class == "synthetic" for case in first.cases)
    assert all(
        tuple(message.role for message in case.messages) == ("system", "system", "user")
        for case in first.cases
    )
    assert not any(
        "agency" in message.content.casefold() for case in first.cases for message in case.messages
    )


def test_benchmark_case_bands_and_abstention_contract(
    make_sar_input: Callable[..., SarInput],
) -> None:
    case = build_benchmark_case(
        case_id="case-long",
        case_set="measured",
        source_dataset="fixture",
        sar_input=make_sar_input(amount="100000.00"),
        required_facts=("USD",),
        expected_citation_ids=("31 CFR 1010.314",),
        history_length=10,
        payment_format="wire",
    )
    assert case.amount_band == "100k-plus"
    assert case.history_length_band == "long"
    assert case.prompt_chars == sum(len(message.content) for message in case.messages)
    assert "txn.amount" in case.available_evidence_refs
    assert "risk.fraudProbability" in case.available_evidence_refs

    abstention = build_benchmark_case(
        case_id="case-abstain",
        case_set="abstention",
        source_dataset="fixture",
        sar_input=make_sar_input(citations=(), rag_context=""),
        required_facts=(),
        expected_citation_ids=(),
        history_length=0,
        payment_format="wire",
    )
    assert abstention.available_evidence_refs == ()
    assert abstention.history_length_band == "none"


def test_fixture_builder_stops_when_context_limit_excludes_quota() -> None:
    config = load_config()
    server = config.server.model_copy(update={"max_model_len": config.request.max_tokens + 1})
    constrained = config.model_copy(update={"server": server})
    with pytest.raises(ValueError, match="fixture case quota deficient"):
        build_fixture_cases(constrained, profile="smoke", repo_root=Path.cwd())
