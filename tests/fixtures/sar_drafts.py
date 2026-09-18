"""Model-output fixtures for the SAR paths that now run behind the deterministic quality gate.

A drafter test needs compact model output that hydrates into a production-gate-passing draft,
otherwise every assertion about masking, cost, caching, or fallback would prove nothing. The full
content still reuses `drafter_mock._compose_content`; the provider JSON projects only the prose and
citation fields the v5 generation boundary permits the model to control.
"""

from __future__ import annotations

from fraudlens_backend.sar.drafter_mock import _compose_content
from fraudlens_backend.sar.egress import load_egress_policy, project_for_model
from fraudlens_backend.sar.evidence import build_evidence_catalog
from fraudlens_backend.sar.schema import SarGenerationContent, SarSectionBodies
from fraudlens_ml.sar import SarDraftContent, SarInput

FABRICATED_CITATION_ID = "99 FAKE 1"


def gate_passing_content(sar_input: SarInput) -> SarDraftContent:
    """Compose the structured body a gate-passing model would return for this input."""
    model_input = project_for_model(sar_input, load_egress_policy())
    catalog = build_evidence_catalog(model_input)
    return _compose_content(sar_input, model_input, catalog)


def gate_passing_json(sar_input: SarInput) -> str:
    """Return gate-passing model output as the exact JSON a provider would stream back."""
    return gate_passing_generation(sar_input).model_dump_json(by_alias=True)


def gate_passing_generation(sar_input: SarInput) -> SarGenerationContent:
    """Project a trusted full fixture onto the compact v5 provider response boundary."""
    content = gate_passing_content(sar_input)
    bodies = {section.heading.lower(): section.body for section in content.sections}
    return SarGenerationContent(
        subject=content.subject,
        narrative=content.narrative,
        claim_statement=content.claims[0].statement,
        section_bodies=SarSectionBodies.model_validate(bodies),
        citation_ids=content.cited_regulations,
    )


def fabricated_citation_json(sar_input: SarInput) -> str:
    """Return otherwise-valid model output that also cites an id never offered."""
    generated = gate_passing_generation(sar_input)
    return generated.model_copy(
        update={"citation_ids": (*generated.citation_ids, FABRICATED_CITATION_ID)}
    ).model_dump_json(by_alias=True)
