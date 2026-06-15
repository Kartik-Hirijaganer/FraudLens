"""Unit tests for the versioned SAR prompt loader + PHI-safe assembly (plan §7.3, §8.1)."""

from __future__ import annotations

import pytest

from fraudlens_backend.sar.prompt import (
    SarPromptTemplate,
    _split_front_matter,
    build_messages,
)


def test_load_records_version_and_stable_hash() -> None:
    first = SarPromptTemplate.load()
    second = SarPromptTemplate.load()
    assert first.template_id == "v1"
    assert first.prompt_version == "v1@1.0.0"
    assert len(first.prompt_hash) == 64
    assert first.prompt_hash == second.prompt_hash  # deterministic for the same template bytes
    assert first.system_text  # body present


def test_build_messages_masks_phi_and_fences_regulations(make_sar_input) -> None:
    # Defense-in-depth: even if PHI-shaped text reaches a rendered field (here the retrieved
    # regulation block), the assembly masks it before the prompt leaves this module (plan §7.8).
    sar_input = make_sar_input(
        rag_context="<<REGS>>\nreach analyst@example.com SSN 123-45-6789\n<<END>>",
    )
    messages = build_messages(SarPromptTemplate.load(), sar_input)
    assert [m["role"] for m in messages] == ["system", "user"]
    user = messages[1]["content"]
    assert "analyst@example.com" not in user
    assert "123-45-6789" not in user
    assert "[REDACTED_EMAIL]" in user and "[REDACTED_SSN]" in user
    assert "<<REGS>>" in user  # the pre-fenced RAG-as-data block is embedded
    assert "STRUCT" in user  # the rule indicator is surfaced


def test_build_messages_handles_empty_rules_features_citations(make_sar_input) -> None:
    sar_input = make_sar_input(rule_hits=(), top_features=(), citations=(), rag_context="")
    user = build_messages(SarPromptTemplate.load(), sar_input)[1]["content"]
    assert "Rule indicators: none fired." in user
    assert "Regulations: none available" in user


def test_build_messages_lists_citations_without_rag_block(make_sar_input) -> None:
    # Citations present but no retrieved excerpt block: the ids are still listed, no fence embedded.
    user = build_messages(SarPromptTemplate.load(), make_sar_input(rag_context=""))[1]["content"]
    assert "31 CFR 1010.314: Structuring transactions" in user
    assert "<<" not in user


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
