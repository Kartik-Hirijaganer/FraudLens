"""Summary: Typed synthetic adversarial and clean SAR drafts for the hallucination gate.

Key classes:
- AdversarialDraftCase: a draft plus the claim indexes intentionally left unsupported.

Key functions:
- adversarial_draft_cases: build amount, date, typology, and citation adversaries.
- clean_draft: build supported claims used to measure false positives.

Notes:
- All evidence is conspicuously synthetic and contains no identifiers or real-person data.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from fraudlens_ml.sar import SarClaim, SarDraftContent

SUPPORTED_REF = "case-evidence-supported"


class AdversarialDraftCase(BaseModel):
    """One draft with stable planted unsupported-claim indexes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., min_length=1, description="Stable adversarial case name.")
    content: SarDraftContent = Field(..., description="Draft evaluated by deterministic checks.")
    unsupported_indexes: tuple[int, ...] = Field(
        ..., min_length=1, description="Indexes deliberately lacking resolvable evidence."
    )


def _content(
    unsupported_statement: str, *, citation_id: str = "31 CFR 1010.314"
) -> SarDraftContent:
    return SarDraftContent(
        subject="Synthetic activity review",
        narrative=f"Supported evidence was reviewed. {unsupported_statement}",
        claims=(
            SarClaim(
                statement="The deterministic evidence requires human review.",
                evidence_refs=(SUPPORTED_REF,),
                citation_ids=("31 CFR 1010.314",),
            ),
            SarClaim(statement=unsupported_statement, citation_ids=(citation_id,)),
        ),
        cited_regulations=(citation_id,),
        recommended_action="Escalate for human review.",
    )


def adversarial_draft_cases() -> tuple[AdversarialDraftCase, ...]:
    """Return the deterministic unsupported amount/date/typology/citation corpus."""
    cases = (
        ("altered-amount", "The amount was 91,000 USD."),
        ("altered-date", "The transaction occurred on 2099-12-31."),
        ("invented-typology", "The activity was confirmed terrorist financing."),
        ("unsupported-identity", "A named beneficial owner directed the transfer."),
        ("unsupported-counterparty", "Counterparty account 4111111111111111 received funds."),
    )
    return (
        *(
            AdversarialDraftCase(name=name, content=_content(statement), unsupported_indexes=(1,))
            for name, statement in cases
        ),
        AdversarialDraftCase(
            name="fabricated-citation",
            content=_content(
                "An unavailable regulation governs the activity.", citation_id="FAKE-1"
            ),
            unsupported_indexes=(1,),
        ),
    )


def clean_draft() -> SarDraftContent:
    """Return a draft whose two claims resolve to the supplied evidence reference."""
    return SarDraftContent(
        subject="Synthetic activity review",
        narrative="The amount and risk result require human review.",
        claims=(
            SarClaim(statement="The amount was 9,500 USD.", evidence_refs=(SUPPORTED_REF,)),
            SarClaim(statement="The risk band was high.", evidence_refs=(SUPPORTED_REF,)),
        ),
        cited_regulations=("31 CFR 1010.314",),
        recommended_action="Escalate for human review.",
    )
