"""fraudlens-ml SAR-drafting contract (plan §7, §16 Phase 7): the injected `SarDrafter`
protocol + its PHI-free value types. This is the seam that lets SAR drafting reach ml without
ml importing fraudlens-llm/fraudlens-backend (ruff-enforced layering); the concrete mock/live
drafters live in the backend. Dependency-light on purpose — imports fraudlens-core + pydantic
only (never fraudlens_ml.scoring/rag), so importing it never drags in xgboost/shap/chromadb.
Re-exports are intentional (the public SAR contract surface)."""

from __future__ import annotations

from fraudlens_ml.sar.protocol import (
    SarAgentEvent,
    SarCitation,
    SarClaim,
    SarDraftContent,
    SarDrafter,
    SarDraftResult,
    SarDraftStatus,
    SarEventType,
    SarFeature,
    SarInput,
    SarSection,
    SarStreamEvent,
    SarTokenUsage,
)

__all__ = [
    "SarAgentEvent",
    "SarCitation",
    "SarClaim",
    "SarDraftContent",
    "SarDraftResult",
    "SarDraftStatus",
    "SarDrafter",
    "SarEventType",
    "SarFeature",
    "SarInput",
    "SarSection",
    "SarStreamEvent",
    "SarTokenUsage",
]
