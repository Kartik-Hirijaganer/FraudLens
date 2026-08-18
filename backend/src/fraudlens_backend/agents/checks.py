"""Summary: Deterministic grounding gates for the bounded multi-agent SAR workflow.
The checks run before compliance review so claim support and citation membership
are decided by code rather than delegated to a model.

Key classes:
- DeterministicReviewChecks: immutable result supplied to the reviewer and router.

Key functions:
- evaluate_draft_checks: verify claim evidence and citation membership.

Notes:
- These checks do not ground or mutate the draft; fabricated ids remain visible to the reviewer.
"""

from __future__ import annotations

from collections.abc import Collection

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from fraudlens_ml.sar import SarCitation, SarDraftContent


class DeterministicReviewChecks(BaseModel):
    """Immutable deterministic findings used by review routing and the reviewer prompt."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )

    passed: bool = Field(..., description="Whether every deterministic grounding gate passed.")
    every_claim_has_evidence: bool = Field(
        ..., description="Whether every narrative claim carries an evidence reference."
    )
    cited_ids_are_available: bool = Field(
        ..., description="Whether every draft citation id exists in the supplied corpus evidence."
    )
    evidence_refs_are_available: bool = Field(
        ...,
        description="Whether every claim evidence reference resolves to trusted persisted data.",
    )
    unsupported_claim_indexes: tuple[int, ...] = Field(
        default=(),
        description="Zero-based indexes of claims without resolvable evidence references.",
    )
    unresolved_evidence_refs: tuple[str, ...] = Field(
        default=(),
        description="Ordered claim evidence references absent from trusted persisted data.",
    )
    fabricated_citation_ids: tuple[str, ...] = Field(
        default=(), description="Ordered draft citation ids absent from supplied corpus evidence."
    )


def evaluate_draft_checks(
    content: SarDraftContent,
    available: tuple[SarCitation, ...],
    *,
    available_evidence_refs: Collection[str] | None = None,
) -> DeterministicReviewChecks:
    """Evaluate evidence presence and citation membership without changing the draft."""
    unresolved = (
        tuple(
            dict.fromkeys(
                evidence_ref
                for claim in content.claims
                for evidence_ref in claim.evidence_refs
                if evidence_ref not in available_evidence_refs
            )
        )
        if available_evidence_refs is not None
        else ()
    )
    unresolved_set = set(unresolved)
    missing_evidence = tuple(
        index for index, claim in enumerate(content.claims) if not claim.evidence_refs
    )
    unsupported = tuple(
        index
        for index, claim in enumerate(content.claims)
        if not claim.evidence_refs
        or any(evidence_ref in unresolved_set for evidence_ref in claim.evidence_refs)
    )
    available_ids = {citation.citation for citation in available}
    claimed_ids = (
        *content.cited_regulations,
        *(citation_id for claim in content.claims for citation_id in claim.citation_ids),
    )
    fabricated = tuple(dict.fromkeys(item for item in claimed_ids if item not in available_ids))
    every_claim_has_evidence = not missing_evidence
    cited_ids_are_available = not fabricated
    evidence_refs_are_available = not unresolved
    return DeterministicReviewChecks(
        passed=(
            every_claim_has_evidence
            and evidence_refs_are_available
            and not unsupported
            and cited_ids_are_available
        ),
        every_claim_has_evidence=every_claim_has_evidence,
        cited_ids_are_available=cited_ids_are_available,
        evidence_refs_are_available=evidence_refs_are_available,
        unsupported_claim_indexes=unsupported,
        unresolved_evidence_refs=unresolved,
        fabricated_citation_ids=fabricated,
    )
