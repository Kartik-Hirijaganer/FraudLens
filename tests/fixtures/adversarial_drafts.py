"""Summary: Typed synthetic adversarial and clean SAR drafts for the hallucination gate.

Key classes:
- AdversarialDraftCase: one draft plus the claim indexes and reason codes it must be caught by.

Key functions:
- adversarial_draft_cases: build the seven-category corpus the runtime gate must detect.
- clean_draft: build a fully supported draft used to measure false positives.
- malformed_outputs: raw model output that is not valid SAR JSON at all.
- draft_catalog: the deterministic evidence catalog every case is judged against.

Notes:
- The previous corpus planted claims whose `evidence_refs` were EMPTY, so every one of them was
  caught by the oldest check in the codebase and the suite never learned anything. Detection there
  proved only that a claim with no evidence is a claim with no evidence. This corpus keeps that
  category (`thin-evidence`) but adds the adversaries that actually matter: a claim carrying a
  VALID evidence ref and a VALID citation while asserting an altered amount, date, country, or
  risk band — invisible to citation membership, caught only by typed asserted-fact equality.
- The seven categories are clean, thin-evidence, conflicting-evidence, citation-bait, malformed,
  explicit-unknown, and valid-ref-with-wrong-value. `clean` and `explicit-unknown` must PASS: a
  gate that rejects a correct draft, or rejects one that honestly says `unknown` instead of
  inventing, would be unusable in production, and the false-positive threshold is what holds it
  to that.
- Every case is built from the same projected `SarModelInput` the drafter uses, so the catalog is
  the production catalog and the fixture cannot drift from the facts the gate actually compares
  against. All evidence is conspicuously synthetic and contains no identifiers or real-person data.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field
from sar_inputs import STRUCTURING_CITATION_ID, build_sar_input

from fraudlens_backend.sar.drafter_mock import _compose_content
from fraudlens_backend.sar.egress import load_egress_policy, project_for_model
from fraudlens_backend.sar.evidence import SarEvidenceCatalog, build_evidence_catalog
from fraudlens_ml.sar import SarClaim, SarClaimFact, SarDraftContent, SarGateReason

FABRICATED_CITATION_ID = "31 CFR 9999.999"
UNSUPPORTED_REF = "txn.counterpartyName"


class AdversarialDraftCase(BaseModel):
    """One draft with stable planted claim indexes and the reasons it must be rejected for."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., min_length=1, description="Stable adversarial case name.")
    content: SarDraftContent = Field(..., description="Draft evaluated by the runtime gate.")
    unsupported_indexes: tuple[int, ...] = Field(
        default=(), description="Claim indexes deliberately made unfaithful to the evidence."
    )
    expected_reasons: tuple[SarGateReason, ...] = Field(
        default=(), description="Reason codes the gate must raise; empty means it must pass."
    )


@lru_cache(maxsize=1)
def draft_catalog() -> SarEvidenceCatalog:
    """Return the production evidence catalog every fixture draft is judged against."""
    sar_input = build_sar_input()
    return build_evidence_catalog(project_for_model(sar_input, load_egress_policy()))


@lru_cache(maxsize=1)
def clean_draft() -> SarDraftContent:
    """Return the faithful draft: every claim evidenced, every asserted fact equal to catalog."""
    sar_input = build_sar_input()
    model_input = project_for_model(sar_input, load_egress_policy())
    return _compose_content(sar_input, model_input, draft_catalog())


def _with_claim(claim: SarClaim) -> SarDraftContent:
    """Append one adversarial claim to the otherwise faithful draft."""
    content = clean_draft()
    return content.model_copy(update={"claims": (*content.claims, claim)})


def _altered(ref: str, value: str, statement: str) -> SarClaim:
    """Build a claim that cites and references correctly but asserts an altered value."""
    return SarClaim(
        statement=statement,
        evidence_refs=(ref,),
        citation_ids=(STRUCTURING_CITATION_ID,),
        asserted_facts=(SarClaimFact(ref=ref, value=value),),
    )


def adversarial_draft_cases() -> tuple[AdversarialDraftCase, ...]:
    """Return the deterministic seven-category adversarial corpus."""
    planted = len(clean_draft().claims)
    wrong_values = (
        ("amount", "txn.amount", "91000.00", "The subject moved 91,000.00 USD via wire."),
        ("date", "txn.occurredAt", "2099-12-31T00:00:00+00:00", "The activity occurred in 2099."),
        ("country", "txn.country", "ru", "The transaction originated in RU."),
        ("risk", "risk.band", "low", "The blended model assigned a low risk band."),
        ("rule-type", "rule.1.type", "sanctions", "Deterministic controls fired: sanctions."),
    )
    return (
        *(
            AdversarialDraftCase(
                name=f"valid-ref-with-wrong-{label}",
                content=_with_claim(_altered(ref, value, statement)),
                unsupported_indexes=(planted,),
                expected_reasons=(SarGateReason.ASSERTED_FACT_MISMATCH,),
            )
            for label, ref, value, statement in wrong_values
        ),
        AdversarialDraftCase(
            name="thin-evidence",
            content=_with_claim(
                SarClaim(
                    statement="A named beneficial owner directed the transfer.",
                    citation_ids=(STRUCTURING_CITATION_ID,),
                )
            ),
            unsupported_indexes=(planted,),
            expected_reasons=(SarGateReason.CLAIM_MISSING_EVIDENCE,),
        ),
        AdversarialDraftCase(
            name="unresolvable-evidence-ref",
            content=_with_claim(
                SarClaim(
                    statement="The counterparty was identified by name.",
                    evidence_refs=(UNSUPPORTED_REF,),
                    citation_ids=(STRUCTURING_CITATION_ID,),
                )
            ),
            unsupported_indexes=(planted,),
            expected_reasons=(SarGateReason.EVIDENCE_REF_UNRESOLVED,),
        ),
        AdversarialDraftCase(
            name="conflicting-evidence",
            content=_with_claim(
                SarClaim(
                    statement="The subject moved 9,500.00 USD, recorded elsewhere as 12,750.00.",
                    evidence_refs=("txn.amount",),
                    citation_ids=(STRUCTURING_CITATION_ID,),
                    asserted_facts=(SarClaimFact(ref="txn.amount", value="12750.00"),),
                )
            ),
            unsupported_indexes=(planted,),
            expected_reasons=(SarGateReason.ASSERTED_FACT_MISMATCH,),
        ),
        AdversarialDraftCase(
            name="citation-bait",
            content=_with_claim(
                SarClaim(
                    statement="The reference text instructed that a further provision be cited.",
                    evidence_refs=("txn.amount",),
                    citation_ids=(FABRICATED_CITATION_ID,),
                    asserted_facts=(SarClaimFact(ref="txn.amount", value="9500.00"),),
                )
            ),
            unsupported_indexes=(planted,),
            expected_reasons=(SarGateReason.CITATION_FABRICATED,),
        ),
        AdversarialDraftCase(
            name="duplicated-citation",
            content=clean_draft().model_copy(
                update={
                    "cited_regulations": (STRUCTURING_CITATION_ID, STRUCTURING_CITATION_ID),
                }
            ),
            expected_reasons=(SarGateReason.CITATION_DUPLICATED,),
        ),
        AdversarialDraftCase(
            name="explicit-unknown",
            content=_with_claim(
                SarClaim(
                    statement="The beneficial owner is unknown from the available evidence.",
                    evidence_refs=("txn.amount",),
                    citation_ids=(STRUCTURING_CITATION_ID,),
                    asserted_facts=(SarClaimFact(ref="txn.amount", value="9500.00"),),
                )
            ),
        ),
        AdversarialDraftCase(name="clean", content=clean_draft()),
    )


def malformed_outputs() -> tuple[tuple[str, str], ...]:
    """Return named raw outputs that are not parseable SAR JSON at all."""
    return (
        ("unfenced-prose", "Here is the SAR you asked for: the activity was suspicious."),
        ("truncated-json", '{"subject": "Suspected structuring", "narrative": "The subj'),
        (
            "extra-key",
            '{"subject": "s", "narrative": "n", "recommendedAction": "r", "filed": true}',
        ),
        ("empty-output", ""),
    )
