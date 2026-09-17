"""Unit tests for the versioned SAR prompt loader + PHI-safe assembly (plan §7.3, §8.1)."""

from __future__ import annotations

import pytest

from fraudlens_backend.sar.egress import load_egress_policy, project_for_model
from fraudlens_backend.sar.prompt import (
    SarPromptTemplate,
    _split_front_matter,
    build_messages,
)


def _messages(sar_input):
    return build_messages(
        SarPromptTemplate.load(), project_for_model(sar_input, load_egress_policy())
    )


def test_load_records_version_and_stable_hash() -> None:
    first = SarPromptTemplate.load()
    second = SarPromptTemplate.load()
    assert first.template_id == "v5"
    assert first.prompt_version == "v5@5.0.0"
    assert len(first.prompt_hash) == 64
    assert first.prompt_hash == second.prompt_hash  # deterministic for the same template bytes
    assert first.system_text  # body present


def test_v1_remains_loadable_and_distinct_from_the_default() -> None:
    """v1 stays on disk: the published vLLM benchmark's promptSha256 binds to its exact bytes."""
    legacy = SarPromptTemplate.load("v1")
    assert legacy.prompt_version == "v1@1.0.0"
    assert legacy.prompt_hash != SarPromptTemplate.load().prompt_hash


def test_adverse_live_prompt_v2_remains_loadable_for_lineage() -> None:
    prior = SarPromptTemplate.load("v2")
    assert prior.prompt_version == "v2@2.0.0"
    assert prior.prompt_hash != SarPromptTemplate.load().prompt_hash


def test_adverse_live_prompt_v3_remains_loadable_for_lineage() -> None:
    prior = SarPromptTemplate.load("v3")
    assert prior.prompt_version == "v3@3.0.0"
    assert prior.prompt_hash != SarPromptTemplate.load().prompt_hash


def test_high_latency_live_prompt_v4_remains_loadable_for_lineage() -> None:
    prior = SarPromptTemplate.load("v4")
    assert prior.prompt_version == "v4@4.0.0"
    assert prior.prompt_hash != SarPromptTemplate.load().prompt_hash


def test_build_messages_masks_phi_and_fences_regulations(make_sar_input) -> None:
    # Defense-in-depth: even if PHI-shaped text reaches a rendered field (here the retrieved
    # regulation block), the assembly masks it before the prompt leaves this module (plan §7.8).
    sar_input = make_sar_input(
        rag_context="<<REGS>>\nreach analyst@example.com SSN 123-45-6789\n<<END>>",
    )
    messages = _messages(sar_input)
    assert [m["role"] for m in messages] == ["system", "user"]
    user = messages[1]["content"]
    assert "analyst@example.com" not in user
    assert "123-45-6789" not in user
    assert "[REDACTED_EMAIL]" not in user and "[REDACTED_SSN]" not in user
    assert "<<REGS>>" not in user  # raw RAG context is outside the outbound allowlist
    assert "structuring" in user  # the controlled rule type is surfaced


def test_build_messages_handles_empty_rules_features_citations(make_sar_input) -> None:
    sar_input = make_sar_input(rule_hits=(), top_features=(), citations=(), rag_context="")
    user = _messages(sar_input)[1]["content"]
    assert "Rule indicators: none fired." in user
    assert "Regulations: none available" in user


def test_build_messages_lists_citations_without_rag_block(make_sar_input) -> None:
    # Citations present but no retrieved excerpt block: the ids are still listed, no fence embedded.
    user = _messages(make_sar_input(rag_context=""))[1]["content"]
    assert "31 CFR 1010.314: Structuring transactions to evade" in user
    assert "<<" not in user


def test_build_messages_offers_the_closed_evidence_catalog(make_sar_input) -> None:
    """The prompt must name every ref a claim may cite, or the gate can only ever reject."""
    user = _messages(make_sar_input())[1]["content"]
    assert "Evidence catalog (ref | value | as written):" in user
    assert "- txn.amount | 9500 | 9500.00 USD" in user
    assert "- risk.band | high | high" in user
    assert "- rule.1.type | structuring | structuring" in user
    assert "- regulation.31 CFR 1010.314 |" in user
    assert "Required narrative facts (backend attaches refs; use `as written` in prose):" in user
    assert "- txn.occurredAt | 2024-06-01T14:00:00+00:00 | 2024-06-01T14:00:00" in user


def test_build_messages_reports_an_empty_catalog_explicitly(make_sar_input) -> None:
    user = _messages(make_sar_input(rule_hits=(), top_features=(), citations=(), rag_context=""))[
        1
    ]["content"]
    assert "Evidence catalog (ref | value | as written):" in user  # transaction facts always exist
    assert "regulation." not in user


def test_split_front_matter_requires_opening_fence() -> None:
    with pytest.raises(ValueError, match="missing its '---'"):
        _split_front_matter("no front matter here")


def test_split_front_matter_requires_closing_fence() -> None:
    with pytest.raises(ValueError, match="not closed"):
        _split_front_matter("---\nversion: '1.0.0'\nbody without closing fence")


def test_split_front_matter_parses_valid_template() -> None:
    meta, body = _split_front_matter('---\nversion: "2.0.0"\ndescription: "d"\n---\nBODY')
    assert meta.version == "2.0.0"
    assert body.strip() == "BODY"
