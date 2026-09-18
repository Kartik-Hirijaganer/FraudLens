"""Backend SAR-drafting package (plan §7, §16 Phase 7): the concrete mock + live `SarDrafter`
implementations, the quality-gated cascade and its deterministic `SarQualityGate`, the evidence
catalog every claim must match, the versioned prompt loader, the structured-output schema +
citation grounding, the spend budget guard, and the replay cache. The injected `SarDrafter`
protocol itself lives in `fraudlens_ml.sar` so ml never imports fraudlens-llm (layering).
Re-exports are intentional."""

from __future__ import annotations

from fraudlens_backend.sar.budget import (
    BudgetGuard,
    SarBudgetExceededError,
    estimate_cost_usd,
)
from fraudlens_backend.sar.cache import InMemorySarDraftCache, SarDraftCache
from fraudlens_backend.sar.drafter_gated import (
    QualityGatedSarDrafter,
    SarCascadeConfigError,
    SarCascadeTier,
)
from fraudlens_backend.sar.drafter_live import LiveSarDrafter
from fraudlens_backend.sar.drafter_mock import MockSarDrafter
from fraudlens_backend.sar.drafter_replay import PersistedSarDrafter, resume_drafter
from fraudlens_backend.sar.evidence import SarEvidenceCatalog, build_evidence_catalog
from fraudlens_backend.sar.factory import (
    SarLlmConfig,
    SarProfileNotSelectedError,
    SarTierConfig,
    build_sar_drafter,
    load_sar_llm_config,
)
from fraudlens_backend.sar.prompt import SarPromptMeta, SarPromptTemplate, build_messages
from fraudlens_backend.sar.quality_gate import (
    SarFincenElement,
    SarQualityGate,
    SarQualityPolicyError,
    SarRuntimeGatePolicy,
    load_sar_gate_policy,
)
from fraudlens_backend.sar.schema import (
    SarSchemaError,
    ground_citations,
    parse_and_ground,
    parse_only,
    render_markdown,
    sar_response_schema,
)

__all__ = [
    "BudgetGuard",
    "InMemorySarDraftCache",
    "LiveSarDrafter",
    "MockSarDrafter",
    "PersistedSarDrafter",
    "QualityGatedSarDrafter",
    "SarBudgetExceededError",
    "SarCascadeConfigError",
    "SarCascadeTier",
    "SarDraftCache",
    "SarEvidenceCatalog",
    "SarFincenElement",
    "SarLlmConfig",
    "SarProfileNotSelectedError",
    "SarPromptMeta",
    "SarPromptTemplate",
    "SarQualityGate",
    "SarQualityPolicyError",
    "SarRuntimeGatePolicy",
    "SarSchemaError",
    "SarTierConfig",
    "build_evidence_catalog",
    "build_messages",
    "build_sar_drafter",
    "estimate_cost_usd",
    "ground_citations",
    "load_sar_gate_policy",
    "load_sar_llm_config",
    "parse_and_ground",
    "parse_only",
    "render_markdown",
    "resume_drafter",
    "sar_response_schema",
]
