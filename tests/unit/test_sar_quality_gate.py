"""Unit tests for the production SARQualityGate and its deterministic evidence catalog.

Release 0.5.0 Phase 2.3: every rule is set membership, a count, a typed equality, or an enum
comparison — no model, no IO, no ground truth. These tests pin the rules that did not exist
before: typed asserted-fact equality (a wrong amount carrying a VALID citation), narrative
mapping, citation duplication, FinCEN coverage, and truncation.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from quality_gates import production_gate
from sar_inputs import build_sar_input

from fraudlens_backend.sar.drafter_mock import _compose_content
from fraudlens_backend.sar.egress import load_egress_policy, project_for_model
from fraudlens_backend.sar.evidence import _fact, build_evidence_catalog
from fraudlens_backend.sar.quality_gate import (
    SarFincenElement,
    SarQualityGate,
    SarQualityPolicyError,
    SarRuntimeGatePolicy,
    load_sar_gate_policy,
)
from fraudlens_backend.sar.schema import sar_response_schema
from fraudlens_ml.sar import (
    SarClaim,
    SarClaimFact,
    SarDraftContent,
    SarFactKind,
    SarGateReason,
    SarSection,
    canonical_fact_value,
)


def _case(make_sar_input, **overrides):
    """Return one projected case: its input, evidence catalog, and a gate-passing draft."""
    sar_input = make_sar_input(**overrides)
    model_input = project_for_model(sar_input, load_egress_policy())
    catalog = build_evidence_catalog(model_input)
    return sar_input, catalog, _compose_content(sar_input, model_input, catalog)


def _evaluate(gate: SarQualityGate, sar_input, catalog, content, **kwargs):
    return gate.evaluate(content, available=sar_input.citations, catalog=catalog, **kwargs)


def test_policy_round_trips_from_the_committed_quality_config() -> None:
    policy = load_sar_gate_policy()
    assert policy.policy_version == "sar-gate-v1"
    assert policy.require_citation and policy.require_claim_evidence
    assert policy.require_asserted_fact_match and policy.require_fincen_elements
    assert policy.fail_on_truncation and not policy.allow_duplicate_citations
    assert len(policy.policy_hash) == 64


def test_policy_load_fails_closed_on_a_malformed_file(tmp_path) -> None:
    broken = tmp_path / "quality.yaml"
    broken.write_text("sar_quality: {}\n", encoding="utf-8")
    with pytest.raises(SarQualityPolicyError):
        load_sar_gate_policy(broken)


def test_a_clean_draft_passes_every_enabled_rule(make_sar_input) -> None:
    sar_input, catalog, content = _case(make_sar_input)

    verdict = _evaluate(production_gate(), sar_input, catalog, content)

    assert verdict.passed is True
    assert verdict.reasons == ()
    assert verdict.fallback_required is False
    assert verdict.citation_metrics.precision == 1.0
    assert verdict.policy_version == "sar-gate-v1"


def test_the_verdict_is_byte_identical_across_repeated_evaluations(make_sar_input) -> None:
    """A gate verdict is replayable evidence, so it must not vary with iteration order."""
    sar_input, catalog, content = _case(make_sar_input)
    gate = production_gate()

    verdicts = {_evaluate(gate, sar_input, catalog, content).model_dump_json() for _ in range(100)}

    assert len(verdicts) == 1


def test_a_fabricated_citation_is_rejected_and_named(make_sar_input) -> None:
    sar_input, catalog, content = _case(make_sar_input)
    fabricated = content.model_copy(
        update={"cited_regulations": (*content.cited_regulations, "99 FAKE 1")}
    )

    verdict = _evaluate(production_gate(), sar_input, catalog, fabricated)

    assert SarGateReason.CITATION_FABRICATED in verdict.reasons
    assert verdict.fabricated_citation_ids == ("99 FAKE 1",)
    assert verdict.fallback_required is True


def test_a_repeated_id_within_one_list_is_a_duplicate(make_sar_input) -> None:
    sar_input, catalog, content = _case(make_sar_input)
    repeated = content.model_copy(update={"cited_regulations": content.cited_regulations * 2})

    verdict = _evaluate(production_gate(), sar_input, catalog, repeated)

    assert SarGateReason.CITATION_DUPLICATED in verdict.reasons
    assert verdict.duplicate_citation_ids == content.cited_regulations


def test_an_id_shared_between_a_claim_and_the_top_level_is_not_a_duplicate(
    make_sar_input,
) -> None:
    """A claim legitimately re-states a regulation the draft also lists; that is not fabrication."""
    sar_input, catalog, content = _case(make_sar_input)

    verdict = _evaluate(production_gate(), sar_input, catalog, content)

    assert content.claims[0].citation_ids == content.cited_regulations
    assert verdict.duplicate_citation_ids == ()


def test_a_draft_with_no_citations_is_rejected(make_sar_input) -> None:
    sar_input, catalog, content = _case(make_sar_input)
    uncited = content.model_copy(
        update={
            "cited_regulations": (),
            "claims": tuple(
                claim.model_copy(update={"citation_ids": ()}) for claim in content.claims
            ),
        }
    )

    verdict = _evaluate(production_gate(), sar_input, catalog, uncited)

    assert SarGateReason.NO_CITATIONS in verdict.reasons


def test_an_uncited_draft_passes_when_the_policy_does_not_require_a_citation(
    make_sar_input,
) -> None:
    """A case whose RAG retrieval offered nothing can still produce a serviceable SAR."""
    sar_input, catalog, content = _case(make_sar_input, citations=(), rag_context="")
    uncited = content.model_copy(
        update={
            "cited_regulations": (),
            "claims": tuple(
                claim.model_copy(update={"citation_ids": ()}) for claim in content.claims
            ),
        }
    )
    policy = production_gate().policy
    permissive = SarQualityGate(
        SarRuntimeGatePolicy.model_validate(policy.model_dump() | {"require_citation": False})
    )

    assert (
        SarGateReason.NO_CITATIONS
        in _evaluate(production_gate(), sar_input, catalog, uncited).reasons
    )
    verdict = _evaluate(permissive, sar_input, catalog, uncited)
    assert verdict.passed
    assert verdict.reasons == ()


def test_an_uncitable_case_is_terminal_rather_than_escalatable(make_sar_input) -> None:
    """Nothing was offered to cite, so no later tier can cite either: escalating is pure spend."""
    sar_input, catalog, content = _case(make_sar_input, citations=(), rag_context="")

    verdict = _evaluate(production_gate(), sar_input, catalog, content)

    assert sar_input.citations == ()
    assert SarGateReason.NO_CITATIONS in verdict.reasons
    assert verdict.fallback_required is False


def test_a_draft_that_ignores_offered_citations_stays_escalatable(make_sar_input) -> None:
    """A model that had citations and used none is a model failure another tier may fix."""
    sar_input, catalog, content = _case(make_sar_input)
    uncited = content.model_copy(
        update={
            "cited_regulations": (),
            "claims": tuple(
                claim.model_copy(update={"citation_ids": ()}) for claim in content.claims
            ),
        }
    )

    verdict = _evaluate(production_gate(), sar_input, catalog, uncited)

    assert sar_input.citations != ()
    assert SarGateReason.NO_CITATIONS in verdict.reasons
    assert verdict.fallback_required is True


def test_a_schema_rejection_stays_escalatable_even_with_nothing_offered() -> None:
    """Only the uncitable reason flips; a malformed answer is still worth asking again for."""
    verdict = production_gate().rejected(SarGateReason.SCHEMA_INVALID)

    assert verdict.reasons == (SarGateReason.SCHEMA_INVALID,)
    assert verdict.fallback_required is True


def test_a_claim_without_evidence_is_rejected(make_sar_input) -> None:
    sar_input, catalog, content = _case(make_sar_input)
    unsupported = content.model_copy(
        update={
            "claims": (
                SarClaim(
                    statement="Unsupported assertion.",
                    citation_ids=content.cited_regulations,
                ),
            )
        }
    )

    verdict = _evaluate(production_gate(), sar_input, catalog, unsupported)

    assert SarGateReason.CLAIM_MISSING_EVIDENCE in verdict.reasons
    assert verdict.unsupported_claim_indexes == (0,)


def test_an_unresolvable_evidence_ref_is_rejected(make_sar_input) -> None:
    sar_input, catalog, content = _case(make_sar_input)
    forged = content.model_copy(
        update={
            "claims": (
                content.claims[0].model_copy(update={"evidence_refs": ("txn.notARealRef",)}),
            )
        }
    )

    verdict = _evaluate(production_gate(), sar_input, catalog, forged)

    assert SarGateReason.EVIDENCE_REF_UNRESOLVED in verdict.reasons


def test_no_trusted_evidence_is_terminal_rather_than_escalatable(make_sar_input) -> None:
    """A case with no evidence fails identically at every tier, so escalating would only spend."""
    sar_input, catalog, content = _case(make_sar_input)

    verdict = _evaluate(production_gate(), sar_input, catalog, content, available_evidence_refs=())

    assert SarGateReason.EVIDENCE_EMPTY in verdict.reasons
    assert verdict.fallback_required is False


@pytest.mark.parametrize(
    ("ref", "wrong_value"),
    [
        ("txn.amount", "12000.00"),
        ("txn.occurredAt", "2024-06-02T14:00:00+00:00"),
        ("txn.country", "GB"),
        ("risk.band", "low"),
        ("rule.1.type", "velocity"),
    ],
)
def test_an_asserted_fact_that_contradicts_the_catalog_fails_despite_a_valid_citation(
    make_sar_input, ref: str, wrong_value: str
) -> None:
    """The hallucination check: a plausible lie carrying a VALID reference passed every rule."""
    sar_input, catalog, content = _case(make_sar_input)
    lying = content.model_copy(
        update={
            "claims": (
                SarClaim(
                    statement="A claim that restates the evidence incorrectly.",
                    evidence_refs=(ref,),
                    citation_ids=content.cited_regulations,
                    asserted_facts=(SarClaimFact(ref=ref, value=wrong_value),),
                ),
            )
        }
    )

    verdict = _evaluate(production_gate(), sar_input, catalog, lying)

    assert SarGateReason.ASSERTED_FACT_MISMATCH in verdict.reasons
    mismatch = verdict.mismatched_facts[0]
    assert (mismatch.claim_index, mismatch.ref) == (0, ref)
    assert mismatch.expected == catalog.get(ref).value
    assert verdict.checks.every_claim_has_evidence is True  # the claim WAS grounded


def test_an_asserted_fact_against_an_unknown_ref_is_a_mismatch(make_sar_input) -> None:
    sar_input, catalog, content = _case(make_sar_input)
    invented = content.model_copy(
        update={
            "claims": (
                content.claims[0].model_copy(
                    update={"asserted_facts": (SarClaimFact(ref="txn.invented", value="1"),)}
                ),
            )
        }
    )

    verdict = _evaluate(production_gate(), sar_input, catalog, invented)

    assert SarGateReason.ASSERTED_FACT_MISMATCH in verdict.reasons
    assert verdict.mismatched_facts[0].expected == ""


def test_an_equal_value_written_differently_still_matches(make_sar_input) -> None:
    """Normalisation is total, so '9500.00' and '9500' are the same fact, not a hallucination."""
    sar_input, catalog, content = _case(make_sar_input)
    restated = content.model_copy(
        update={
            "claims": (
                content.claims[0].model_copy(
                    update={"asserted_facts": (SarClaimFact(ref="txn.amount", value="9,500.000"),)}
                ),
                *content.claims[1:],
            )
        }
    )

    verdict = _evaluate(production_gate(), sar_input, catalog, restated)

    assert verdict.mismatched_facts == ()


def test_an_objective_narrative_value_with_no_asserted_fact_is_rejected(make_sar_input) -> None:
    sar_input, catalog, content = _case(make_sar_input)
    embellished = content.model_copy(
        update={"narrative": f"{content.narrative} A further 42000.55 moved undetected."}
    )

    verdict = _evaluate(production_gate(), sar_input, catalog, embellished)

    assert SarGateReason.UNMAPPED_NARRATIVE_FACT in verdict.reasons
    assert verdict.unmapped_narrative_spans


def test_a_missing_fincen_element_is_named(make_sar_input) -> None:
    sar_input, catalog, content = _case(make_sar_input)
    trimmed = content.model_copy(
        update={
            "sections": tuple(
                section
                for section in content.sections
                if section.heading.casefold() != SarFincenElement.WHEN.value
            )
        }
    )

    verdict = _evaluate(production_gate(), sar_input, catalog, trimmed)

    assert SarGateReason.FINCEN_ELEMENT_MISSING in verdict.reasons
    assert verdict.missing_fincen_elements == (SarFincenElement.WHEN.value,)


def test_an_element_present_but_empty_does_not_count_as_covered(make_sar_input) -> None:
    sar_input, catalog, content = _case(make_sar_input)
    hollow = content.model_copy(
        update={
            "sections": tuple(
                section.model_copy(update={"body": "  "})
                if section.heading.casefold() == SarFincenElement.WHY.value
                else section
                for section in content.sections
            )
        }
    )

    verdict = _evaluate(production_gate(), sar_input, catalog, hollow)

    assert verdict.missing_fincen_elements == (SarFincenElement.WHY.value,)


def test_a_length_stop_is_treated_as_truncation(make_sar_input) -> None:
    sar_input, catalog, content = _case(make_sar_input)

    verdict = _evaluate(production_gate(), sar_input, catalog, content, finish_reason="length")

    assert SarGateReason.OUTPUT_TRUNCATED in verdict.reasons
    assert verdict.fallback_required is True


def test_a_schema_rejection_carries_a_verdict_without_content() -> None:
    verdict = production_gate().rejected(SarGateReason.SCHEMA_INVALID)

    assert verdict.passed is False
    assert verdict.reasons == (SarGateReason.SCHEMA_INVALID,)
    assert verdict.fallback_required is True
    assert verdict.checks.passed is False


@pytest.mark.parametrize(
    ("kind", "raw", "expected"),
    [
        (SarFactKind.MONEY, "1200.00", "1200"),
        (SarFactKind.MONEY, "$1,200", "1200"),
        (SarFactKind.NUMBER, "9500", "9500"),
        (SarFactKind.INSTANT, "2024-06-01T14:00:00Z", "2024-06-01T14:00:00+00:00"),
        (SarFactKind.INSTANT, "2024-06-01T14:00:00", "2024-06-01T14:00:00+00:00"),
        (SarFactKind.ENUM, " Wire ", "wire"),
    ],
)
def test_normalisation_is_explicit_for_every_kind(
    kind: SarFactKind, raw: str, expected: str
) -> None:
    assert canonical_fact_value(kind, raw) == expected


@pytest.mark.parametrize(
    ("kind", "raw"),
    [
        (SarFactKind.MONEY, "about nine thousand"),
        (SarFactKind.NUMBER, "NaN"),
        (SarFactKind.INSTANT, "last Tuesday"),
        (SarFactKind.ENUM, "   "),
    ],
)
def test_an_unnormalisable_value_is_never_silently_coerced(kind: SarFactKind, raw: str) -> None:
    assert canonical_fact_value(kind, raw) is None


def test_the_catalog_derives_stable_refs_from_the_projection(make_sar_input) -> None:
    _sar_input, catalog, _content = _case(make_sar_input)

    assert catalog.get("txn.amount").value == "9500"
    assert catalog.get("txn.amount").display == "9500.00 USD"
    assert catalog.get("txn.currency").value == "usd"
    assert catalog.get("risk.band").value == "high"
    assert catalog.get("rule.1.type").value == "structuring"
    assert catalog.get("driver.amount_log.value").kind is SarFactKind.NUMBER
    assert catalog.get("regulation.31 CFR 1010.314") is not None
    assert catalog.get("txn.nothing") is None
    assert "txn.occurredAt" in catalog.refs


def test_the_catalog_is_deterministic_for_the_same_projection(make_sar_input) -> None:
    sar_input = make_sar_input()
    policy = load_egress_policy()
    first = build_evidence_catalog(project_for_model(sar_input, policy))
    second = build_evidence_catalog(project_for_model(sar_input, policy))

    assert first.model_dump_json() == second.model_dump_json()


def test_a_draft_that_states_no_objective_value_needs_no_asserted_fact() -> None:
    """The narrative rule constrains objective values, not prose: small ordinals never trip it."""
    gate = production_gate()
    content = SarDraftContent(
        subject="Qualitative review",
        narrative="Three indicators fired and the activity warrants review.",
        claims=(SarClaim(statement="Indicators fired.", evidence_refs=("risk.band",)),),
        sections=tuple(
            SarSection(heading=element.value.capitalize(), body="unknown")
            for element in SarFincenElement
        ),
        recommended_action="Escalate for human review.",
    )

    verdict = gate.evaluate(
        content,
        available=(),
        catalog=build_evidence_catalog(project_for_model(_minimal_input(), load_egress_policy())),
        available_evidence_refs=("risk.band",),
    )

    assert SarGateReason.UNMAPPED_NARRATIVE_FACT not in verdict.reasons
    assert SarGateReason.NO_CITATIONS in verdict.reasons  # it cites nothing, which IS a failure


def _minimal_input():
    """Return a citation-free case used to prove the narrative rule tolerates plain prose."""
    return build_sar_input(citations=(), rule_hits=(), top_features=(), amount=Decimal("1"))


def test_a_fact_whose_value_cannot_be_canonicalised_is_a_construction_error() -> None:
    """A catalog entry is evidence: an unusable value must fail loudly, never become a silent ''."""
    with pytest.raises(ValueError, match="cannot be canonicalised"):
        _fact("txn.amount", SarFactKind.MONEY, "not-a-number", "n/a")


def test_the_response_schema_forbids_any_citation_when_none_are_offered() -> None:
    """With nothing to cite, constrained decoding must make citing structurally impossible."""
    schema = sar_response_schema(())

    cited = schema["properties"]["citedRegulations"]
    claim_ids = schema["$defs"]["SarClaim"]["properties"]["citationIds"]
    assert cited["maxItems"] == 0 and "enum" not in cited["items"]
    assert claim_ids["maxItems"] == 0 and "enum" not in claim_ids["items"]
