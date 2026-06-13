"""Backend SAR-drafting package (plan §7, §16 Phase 7): the concrete mock + live `SarDrafter`
implementations, the versioned prompt loader, the structured-output schema + citation grounding,
the spend budget guard, and the replay cache. The injected `SarDrafter` protocol itself lives in
`fraudlens_ml.sar` so ml never imports fraudlens-llm (layering). Re-exports are intentional."""

from __future__ import annotations

from fraudlens_backend.sar.budget import (
    BudgetGuard,
    SarBudgetExceededError,
    estimate_cost_usd,
)
from fraudlens_backend.sar.cache import InMemorySarDraftCache, SarDraftCache
from fraudlens_backend.sar.drafter_live import LiveSarDrafter
from fraudlens_backend.sar.drafter_mock import MockSarDrafter
from fraudlens_backend.sar.factory import SarLlmConfig, build_sar_drafter, load_sar_llm_config
from fraudlens_backend.sar.prompt import SarPromptMeta, SarPromptTemplate, build_messages
from fraudlens_backend.sar.schema import (
    SarSchemaError,
    ground_citations,
    parse_and_ground,
    render_markdown,
)

__all__ = [
    "BudgetGuard",
    "InMemorySarDraftCache",
    "LiveSarDrafter",
    "MockSarDrafter",
    "SarBudgetExceededError",
    "SarDraftCache",
    "SarLlmConfig",
    "SarPromptMeta",
    "SarPromptTemplate",
    "SarSchemaError",
    "build_messages",
    "build_sar_drafter",
    "estimate_cost_usd",
    "ground_citations",
    "load_sar_llm_config",
    "parse_and_ground",
    "render_markdown",
]
