"""Model-output fixtures for the SAR paths that now run behind the deterministic quality gate.

A drafter test needs model output the production gate ACCEPTS, otherwise every assertion about
masking, cost, caching, or fallback would be made against a rejected draft and prove nothing. The
composition reuses `drafter_mock._compose_content`, so the fixture cannot drift from the shape the
gate actually requires: change the gate and this fixture changes with it or the tests fail loudly.
"""

from __future__ import annotations

from fraudlens_backend.sar.drafter_mock import _compose_content
from fraudlens_backend.sar.egress import load_egress_policy, project_for_model
from fraudlens_backend.sar.evidence import build_evidence_catalog
from fraudlens_ml.sar import SarDraftContent, SarInput

FABRICATED_CITATION_ID = "99 FAKE 1"


def gate_passing_content(sar_input: SarInput) -> SarDraftContent:
    """Compose the structured body a gate-passing model would return for this input."""
    model_input = project_for_model(sar_input, load_egress_policy())
    catalog = build_evidence_catalog(model_input)
    return _compose_content(sar_input, model_input, catalog)


def gate_passing_json(sar_input: SarInput) -> str:
    """Return gate-passing model output as the exact JSON a provider would stream back."""
    return gate_passing_content(sar_input).model_dump_json(by_alias=True)


def fabricated_citation_json(sar_input: SarInput) -> str:
    """Return otherwise-valid model output that also cites an id never offered."""
    content = gate_passing_content(sar_input)
    return content.model_copy(
        update={"cited_regulations": (*content.cited_regulations, FABRICATED_CITATION_ID)}
    ).model_dump_json(by_alias=True)
