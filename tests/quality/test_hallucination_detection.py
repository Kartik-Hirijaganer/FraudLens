"""Summary: Deterministic hallucination recall and clean-draft false-positive quality gate.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- Detection is measured through the production `SarQualityGate`, not through `evaluate_draft_checks`
  alone. That is the whole point of the rewritten corpus: its central adversaries carry a VALID
  evidence ref and a VALID citation while asserting an altered amount, date, country, risk band, or
  rule type, and claim-evidence checking reports nothing about them. Only typed asserted-fact
  equality against the evidence catalog catches those, so measuring recall with the old checker
  would score 0 on exactly the cases this release added.
- The false-positive side measures both a faithful draft and one that explicitly says `unknown`
  rather than inventing. A gate that punishes honest uncertainty would teach the opposite of what
  it is for.
- Malformed output is graded at the parse boundary, where the drafter grades it: unfenced prose, a
  truncated object, an `extra="forbid"` violation, and empty output all become `schema_invalid`.
"""

from __future__ import annotations

import pytest
from adversarial_drafts import (
    FABRICATED_CITATION_ID,
    adversarial_draft_cases,
    clean_draft,
    draft_catalog,
    malformed_outputs,
)
from quality_gates import production_gate
from sar_inputs import build_sar_input

from fraudlens_backend.sar.schema import SarSchemaError, ground_citations, parse_only
from fraudlens_core.phi import mask_text
from fraudlens_ml.evaluation import false_positive_rate, unsupported_claim_recall
from fraudlens_ml.sar import SarGateReason, SarQualityGateResult
from lib.quality.config import load_quality_config

pytestmark = pytest.mark.quality
_PHI_SHAPED_ACCOUNT = "4111111111111111"


def _verdict(content) -> SarQualityGateResult:
    """Evaluate one fixture draft exactly as the production drafter would."""
    return production_gate().evaluate(
        content, available=build_sar_input().citations, catalog=draft_catalog()
    )


def _flagged_indexes(content, verdict: SarQualityGateResult) -> set[int]:
    """Collect every claim index the gate holds against the draft, by any rule.

    A fabricated citation is reported as an ID rather than as a claim index, so the ids are
    attributed back to the claims that carry them — otherwise a citation-bait adversary would
    count as undetected while the gate is in fact rejecting the draft because of it.
    """
    fabricated = set(verdict.fabricated_citation_ids)
    return (
        set(verdict.unsupported_claim_indexes)
        | {mismatch.claim_index for mismatch in verdict.mismatched_facts}
        | {
            index
            for index, claim in enumerate(content.claims)
            if fabricated.intersection(claim.citation_ids)
        }
    )


def test_planted_unsupported_claim_recall_meets_threshold() -> None:
    planted: list[str] = []
    detected: list[str] = []

    for case in adversarial_draft_cases():
        verdict = _verdict(case.content)
        flagged = _flagged_indexes(case.content, verdict)
        planted.extend(f"{case.name}:{index}" for index in case.unsupported_indexes)
        detected.extend(f"{case.name}:{index}" for index in sorted(flagged))

    metrics = unsupported_claim_recall(planted, detected)
    assert planted, "the corpus plants no unfaithful claim"
    assert metrics.rate >= load_quality_config().sar_quality.unsupported_claim_recall_min


def test_every_adversarial_case_is_rejected_for_its_own_reason() -> None:
    """Each adversary is caught by the rule it was built to defeat, not by an unrelated one."""
    for case in adversarial_draft_cases():
        verdict = _verdict(case.content)

        assert verdict.passed is not bool(case.expected_reasons), case.name
        assert set(case.expected_reasons) <= set(verdict.reasons), case.name


def test_an_altered_value_behind_a_valid_reference_is_invisible_without_fact_matching() -> None:
    """The new adversaries defeat citation membership and claim evidence entirely."""
    case = next(
        item for item in adversarial_draft_cases() if item.name == "valid-ref-with-wrong-amount"
    )
    verdict = _verdict(case.content)

    assert verdict.checks.every_claim_has_evidence
    assert verdict.checks.evidence_refs_are_available
    assert verdict.fabricated_citation_ids == ()
    assert verdict.unsupported_claim_indexes == ()
    assert verdict.reasons == (SarGateReason.ASSERTED_FACT_MISMATCH,)
    assert verdict.mismatched_facts[0].ref == "txn.amount"


def test_clean_draft_false_positive_rate_meets_threshold() -> None:
    content = clean_draft()
    verdict = _verdict(content)
    flagged = _flagged_indexes(content, verdict)
    metrics = false_positive_rate(
        tuple(True for _ in content.claims),
        tuple(index in flagged for index, _ in enumerate(content.claims)),
    )

    assert verdict.passed
    assert metrics.rate <= load_quality_config().sar_quality.clean_draft_false_positive_max


def test_an_explicit_unknown_is_not_treated_as_a_hallucination() -> None:
    """Saying `unknown` instead of inventing must not cost a draft its acceptance."""
    case = next(item for item in adversarial_draft_cases() if item.name == "explicit-unknown")
    verdict = _verdict(case.content)

    assert verdict.passed
    assert _flagged_indexes(case.content, verdict) == set()


@pytest.mark.parametrize(("name", "raw"), malformed_outputs())
def test_malformed_output_is_rejected_at_the_parse_boundary(name: str, raw: str) -> None:
    """Unfenced prose, truncation, a forbidden key, and empty output all fail schema validity."""
    with pytest.raises(SarSchemaError):
        parse_only(raw)

    verdict = production_gate().rejected(SarGateReason.SCHEMA_INVALID)
    assert not verdict.passed
    assert verdict.reasons == (SarGateReason.SCHEMA_INVALID,)
    assert verdict.fallback_required, name


def test_fabricated_citation_and_phi_shaped_account_never_survive() -> None:
    available = build_sar_input().citations
    grounded_ids, grounded = ground_citations((FABRICATED_CITATION_ID,), available)
    masked = mask_text(f"Counterparty account {_PHI_SHAPED_ACCOUNT} received funds.").value

    assert grounded_ids == ()
    assert grounded == ()
    assert _PHI_SHAPED_ACCOUNT not in masked
