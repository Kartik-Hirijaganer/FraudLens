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
- AgentDrafterFactory: protocol for constructing one verified run-scoped live agent drafter.

Key functions:
- load_sar_llm_config: load + validate config/llm/sar.yml.
- build_sar_drafter: build the mock or live SarDrafter from settings (mock|live by LLM mode).
- build_agent_drafter_factory: bind shared collaborators and create one run-scoped agent drafter.

Notes:
- The live branch defaults limits to an uncapped `BudgetGuard` and an in-process cache; later
  phases inject `system_config`-sourced session/daily caps and a shared cache without code change.
- The catalog is loaded for COST pricing only (mapping the served model's usage to USD); the
  guardrailed client owns provider routing/governance.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

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

if TYPE_CHECKING:
    from fraudlens_backend.agents.config import AgentsConfig
    from fraudlens_backend.agents.contracts import AgentExecutionRecord
    from fraudlens_backend.agents.resume import AgentExecutionReplayPort
    from fraudlens_backend.agents.tools import EvidenceToolset


class AgentDrafterFactory(Protocol):
    """Construct one live graph drafter from verified run context and evidence tools."""

    def __call__(  # noqa: PLR0913 - explicit run-scoped collaborators.
        self,
        toolset: EvidenceToolset,
        *,
        run_id: uuid.UUID | None = None,
        record_execution: Callable[[AgentExecutionRecord], Awaitable[None]] | None = None,
        replay: AgentExecutionReplayPort | None = None,
        daily_limit_usd: Decimal | None = None,
        daily_spent_usd: Decimal | None = None,
    ) -> SarDrafter:
        """Build one run-scoped drafter; optional arguments preserve direct-test ergonomics."""
        ...


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


def build_agent_drafter_factory(
    *,
    client: LlmClient | None = None,
    catalog: Catalog | None = None,
    config: AgentsConfig | None = None,
    daily_limit_usd: Decimal | None = None,
    daily_spent_provider: Callable[[], Decimal] | None = None,
) -> AgentDrafterFactory:
    """Create run-scoped graph drafters with independent budgets and tenant-bound tools."""
    from fraudlens_backend.agents.config import (  # noqa: PLC0415 - breaks package cycle.
        AgentRole,
        load_agents_config,
    )
    from fraudlens_backend.agents.graph import (  # noqa: PLC0415 - breaks package cycle.
        build_agent_graph,
    )
    from fraudlens_backend.agents.prompts import (  # noqa: PLC0415 - breaks package cycle.
        AgentPromptTemplate,
    )
    from fraudlens_backend.agents.runtime import (  # noqa: PLC0415 - breaks package cycle.
        AgentBudgetExceededError,
        AgentRuntime,
        estimate_workflow_max_cost_usd,
    )
    from fraudlens_backend.sar.drafter_multi_agent import (  # noqa: PLC0415 - breaks package cycle.
        MultiAgentSarDrafter,
    )

    resolved_catalog = catalog or load_catalog(get_llm_settings().catalog_path)
    resolved_client = client or LlmClient.from_settings()

    def build(  # noqa: PLR0913 - explicit run-scoped collaborators.
        toolset: EvidenceToolset,
        *,
        run_id: uuid.UUID | None = None,
        record_execution: Callable[[AgentExecutionRecord], Awaitable[None]] | None = None,
        replay: AgentExecutionReplayPort | None = None,
        daily_limit_usd: Decimal | None = daily_limit_usd,
        daily_spent_usd: Decimal | None = None,
    ) -> SarDrafter:
        """Bind one verified run toolset and reject an over-budget graph before provider access."""
        resolved_config = config or load_agents_config(
            catalog=resolved_catalog,
            available_tools=toolset.registry,
        )
        estimate = estimate_workflow_max_cost_usd(resolved_config, resolved_catalog)
        if estimate > resolved_config.workflow.max_cost_usd_per_investigation:
            raise AgentBudgetExceededError(
                "Agent workflow worst-case cost exceeds its configured cap"
            )
        resolved_daily_spend = (
            daily_spent_usd
            if daily_spent_usd is not None
            else daily_spent_provider()
            if daily_spent_provider is not None
            else None
        )
        if (
            daily_limit_usd is not None
            and (resolved_daily_spend or Decimal("0")) + estimate > daily_limit_usd
        ):
            raise AgentBudgetExceededError("Agent workflow exceeds the tenant daily budget")
        prompts = {
            role: AgentPromptTemplate.load(
                role,
                resolved_config.agents.for_role(role).prompt_id,
            )
            for role in AgentRole
        }
        runtime = AgentRuntime(
            client=resolved_client,
            catalog=resolved_catalog,
            config=resolved_config,
            tool_definitions=toolset.definitions,
            tool_executor=toolset.execute,
        )
        graph = build_agent_graph(
            runtime=runtime,
            config=resolved_config,
            prompts=prompts,
            run_id=run_id,
            record_execution=record_execution,
            replay=replay,
        )
        return MultiAgentSarDrafter(
            graph=graph,
            config=resolved_config,
            prompts=prompts,
            budget=BudgetGuard(
                session_limit_usd=resolved_config.workflow.max_cost_usd_per_investigation,
                daily_limit_usd=daily_limit_usd,
                daily_spent_provider=(
                    (lambda: resolved_daily_spend or Decimal("0"))
                    if daily_limit_usd is not None
                    else None
                ),
            ),
        )

    return build
