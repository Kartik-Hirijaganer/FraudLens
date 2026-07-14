"""Summary: The SAR drafter factory + its config loader (plan §7.2, §7.7, §16 Phase 7). It is the
one place that decides mock vs live from `AppSettings.llm_mode` (`FRAUDLENS_LLM_MODE`) and wires the
live drafter's collaborators, so the rest of the system depends only on the injected
`fraudlens_ml.sar.SarDrafter` protocol — `mock` needs no keys/cost (the local-demo default), `live`
calls a provider. Model selection is config-driven: `load_sar_llm_config` reads
`config/llm/sar.yml` (the model reference, the OpenRouter fallback chain, and the output-token cap)
so no model name is ever hardcoded in source (plan §7.2). Every live collaborator (guardrailed
client, pricing catalog, prompt template, budget guard, replay cache) is overridable for tests/DI.

Key classes:
- SarLlmConfig: the non-secret SAR model selection and generation limits.

Key functions:
- load_sar_llm_config: load + validate config/llm/sar.yml.
- build_sar_drafter: build the mock or live SarDrafter from settings (mock|live by LLM mode).

Notes:
- The live branch defaults limits to an uncapped `BudgetGuard` and an in-process cache; later
  phases inject `system_config`-sourced session/daily caps and a shared cache without code change.
- The catalog is loaded for COST pricing only (mapping the served model's usage to USD); the
  guardrailed client owns provider routing/governance.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from fraudlens_backend.sar.budget import BudgetGuard
from fraudlens_backend.sar.cache import InMemorySarDraftCache, SarDraftCache
from fraudlens_backend.sar.drafter_live import LiveSarDrafter
from fraudlens_backend.sar.drafter_mock import MockSarDrafter
from fraudlens_backend.sar.prompt import SarPromptTemplate
from fraudlens_backend.settings import AppSettings, find_config_dir
from fraudlens_llm import Catalog, LlmClient, TaskType, get_llm_settings, load_catalog
from fraudlens_ml.sar import SarDrafter


class SarLlmConfig(BaseModel):
    """The non-secret SAR model selection loaded from config/llm/sar.yml (no hardcoded ids)."""

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    model: str = Field(..., min_length=1, description="Primary SAR model reference (catalog ref).")
    fallbacks: tuple[str, ...] = Field(
        default=(),
        description="Ordered fallback model references (governance-gated at the client).",
    )
    max_output_tokens: int = Field(..., gt=0, description="Max SAR completion tokens (cost cap).")
    reasoning_effort: str | None = Field(
        default=None,
        min_length=1,
        description="Optional provider reasoning-effort hint for the SAR model.",
    )


def load_sar_llm_config(path: Path | None = None) -> SarLlmConfig:
    """Load + validate the SAR model selection from config/llm/sar.yml."""
    config_path = path or (find_config_dir() / "llm" / "sar.yml")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return SarLlmConfig.model_validate(raw)


def build_sar_drafter(  # noqa: PLR0913 - explicit overridable collaborators (DI; no hidden globals).
    settings: AppSettings,
    *,
    client: LlmClient | None = None,
    catalog: Catalog | None = None,
    prompt: SarPromptTemplate | None = None,
    config: SarLlmConfig | None = None,
    budget: BudgetGuard | None = None,
    cache: SarDraftCache | None = None,
) -> SarDrafter:
    """Build the mock or live SarDrafter selected by `settings.llm_mode` (mock|live)."""
    template = prompt or SarPromptTemplate.load()
    if settings.llm_mode == "mock":
        return MockSarDrafter(template)
    sar_config = config or load_sar_llm_config()
    return LiveSarDrafter(
        client=client or LlmClient.from_settings(),
        catalog=catalog or load_catalog(get_llm_settings().catalog_path),
        prompt=template,
        model=sar_config.model,
        max_output_tokens=sar_config.max_output_tokens,
        reasoning_effort=sar_config.reasoning_effort,
        budget=budget or BudgetGuard(),
        cache=cache or InMemorySarDraftCache(),
        fallbacks=sar_config.fallbacks,
        task_type=TaskType.ANALYSIS,
    )
