"""Summary: Provider-free, evidence-based SAR drafts for egress-ineligible cases.

Key classes:
- TemplateSarDrafter: compose only closed local evidence and run the production quality gate.

Key functions:
- (none)

Notes:
- This path neither changes provenance nor authorizes a model, embedding or agent request.
- Model id and the rendered label survive persistence, review versions and PDF export.
- Missing or tampered citations fail through the normal gate or corpus verification.
"""

from __future__ import annotations

from fraudlens_backend.sar.drafter_mock import MockSarDrafter
from fraudlens_backend.sar.egress import EgressPolicy, SarModelInput, project_for_evidence
from fraudlens_backend.sar.prompt import SarPromptTemplate
from fraudlens_backend.sar.quality_gate import SarQualityGate
from fraudlens_ml.sar import SarInput, SarTokenUsage


class TemplateSarDrafter(MockSarDrafter):
    """Reuse deterministic composition and gating without external-provider authorization."""

    _model_id = "template"
    _label = "**Template-generated draft**\n\n"

    def __init__(
        self, *, gate: SarQualityGate | None = None, egress_policy: EgressPolicy | None = None
    ) -> None:
        """Record the versioned local composition contract, never an unused model prompt."""
        super().__init__(SarPromptTemplate.load("template"), gate=gate, egress_policy=egress_policy)

    def _token_usage(self, rendered: str) -> SarTokenUsage:
        """No model ran, so no provider tokens were consumed."""
        return SarTokenUsage()

    def _project(self, sar_input: SarInput) -> SarModelInput:
        """Prepare local evidence only; never call a provider or modify source provenance."""
        return project_for_evidence(sar_input, self._egress_policy)
