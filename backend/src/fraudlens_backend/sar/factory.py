"""Summary: The SAR drafter factory + its config loader (plan §7.2, §7.7, §16 Phase 7). It is the
one place that decides mock vs live from `AppSettings.llm_mode` (`FRAUDLENS_LLM_MODE`) and wires the
live drafter's collaborators, so the rest of the system depends only on the injected
`fraudlens_ml.sar.SarDrafter` protocol — `mock` needs no keys/cost (the local-demo default), `live`
calls a provider. Model selection is config-driven: `load_sar_llm_config` reads
`config/llm/sar.yml` (the model reference, the transport fallback chain, and the output-token cap)
or a profile file such as `config/llm/sar-vllm.yml` (ordered cascade stages), so no model name is
ever hardcoded in source (plan §7.2). Every live collaborator (guardrailed client, pricing catalog,
prompt template, quality gate, budget guard, replay cache) is overridable for tests/DI.
`AppSettings.sar_config_file` anchors the routing file below config/ and `AppSettings.sar_profile`
names the cascade profile within it.

Key classes:
- SarProfileNotSelectedError:
- AgentDrafterFactory: protocol for constructing one verified run-scoped live agent drafter.
- SarTierConfig: one ordered cascade stage (name, model, connection, decoding mode).
- SarLlmConfig: the non-secret SAR model selection, cascade profiles, and generation limits.

Key functions:
- load_sar_llm_config: load + validate a SAR routing config (single model or cascade profiles).
- build_sar_drafter: build the mock, single-route, or gated cascade drafter from settings.
- build_agent_drafter_factory: bind shared collaborators and create one run-scoped agent drafter.

Notes:
- Routing has ONE shape with two sizes (release 0.5.0 Phase 2.10): a config declaring a single
`model` builds exactly today's `LiveSarDrafter`, and a config declaring `profiles` builds the
same drafter per stage behind `QualityGatedSarDrafter`. There is no feature flag and no parallel
code path — a one-stage profile and a single-model config are the same runtime behaviour.
- Every stage shares one `BudgetGuard`, so the cascade's per-tier budget check enforces a single
per-request cap rather than one cap per model.
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
from typing import TYPE_CHECKING, Protocol, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from fraudlens_backend.sar.budget import BudgetGuard
from fraudlens_backend.sar.cache import InMemorySarDraftCache, SarDraftCache
from fraudlens_backend.sar.drafter_gated import QualityGatedSarDrafter, SarCascadeTier
from fraudlens_backend.sar.drafter_live import LiveSarDrafter
from fraudlens_backend.sar.drafter_mock import MockSarDrafter
from fraudlens_backend.sar.egress import load_egress_policy
from fraudlens_backend.sar.prompt import SarPromptTemplate
from fraudlens_backend.sar.quality_gate import SarQualityGate, load_sar_gate_policy
from fraudlens_backend.settings import AppSettings, _config_anchored, find_config_dir
from fraudlens_llm import Catalog, LlmClient, TaskType, get_llm_settings, load_catalog
from fraudlens_ml.sar import SarDrafter

if TYPE_CHECKING:
    from fraudlens_backend.agents.config import AgentsConfig
    from fraudlens_backend.agents.contracts import AgentExecutionRecord
    from fraudlens_backend.agents.resume import AgentExecutionReplayPort
    from fraudlens_backend.agents.tools import EvidenceToolset


_SINGLE_STAGE = "primary"


class SarProfileNotSelectedError(RuntimeError):
    """Raised when the configured SAR routing profile is missing or unknown."""


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


class SarTierConfig(BaseModel):
    """One ordered cascade stage: which model to call, over which named connection."""

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    name: str = Field(..., min_length=1, description="Stage name recorded on every attempt.")
    model: str = Field(..., min_length=1, description="Catalog model reference for this stage.")
    connection: str | None = Field(
        default=None, description="Named connection route, or None for the provider default."
    )
    constrained_decoding: bool = Field(
        default=False, description="Request a closed citation/evidence schema from this stage."
    )
    requires_egress_class: str | None = Field(
        default=None, description="Data class a case must carry before this stage may run."
    )


class SarLlmConfig(BaseModel):
    """The non-secret SAR routing loaded from config/llm/*.yml (no hardcoded ids)."""

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    model: str | None = Field(
        default=None, min_length=1, description="Single-route SAR model reference (catalog ref)."
    )
    fallbacks: tuple[str, ...] = Field(
        default=(),
        description="Ordered TRANSPORT fallback references (governance-gated at the client).",
    )
    max_output_tokens: int = Field(..., gt=0, description="Max SAR completion tokens (cost cap).")
    reasoning_effort: str | None = Field(
        default=None,
        min_length=1,
        description="Optional provider reasoning-effort hint for the SAR model.",
    )
    profiles: dict[str, tuple[SarTierConfig, ...]] = Field(
        default_factory=dict,
        description="Named ordered cascade profiles; each stage is tried once, in order.",
    )

    @model_validator(mode="after")
    def _one_routing_shape(self) -> SarLlmConfig:
        """Require exactly one routing shape so a stale `model` can never shadow a profile."""
        if bool(self.model) == bool(self.profiles):
            raise ValueError("SAR routing config must declare either 'model' or 'profiles'")
        if any(not stages for stages in self.profiles.values()):
            raise ValueError("Every SAR cascade profile must declare at least one stage")
        return self

    def stages(self, profile: str | None) -> tuple[SarTierConfig, ...]:
        """Return the ordered stages for the selected profile, or the single-model route."""
        if not self.profiles:
            return (SarTierConfig(name=_SINGLE_STAGE, model=cast(str, self.model)),)
        if not profile:
            raise SarProfileNotSelectedError(
                "This SAR routing config declares profiles; set FRAUDLENS_SAR_PROFILE"
            )
        stages = self.profiles.get(profile)
        if stages is None:
            raise SarProfileNotSelectedError(f"SAR profile '{profile}' is not configured")
        return stages


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
    gate: SarQualityGate | None = None,
) -> SarDrafter:
    """Build the mock, single-route, or quality-gated cascade drafter from settings."""
    template = prompt or SarPromptTemplate.load()
    resolved_gate = gate or SarQualityGate(load_sar_gate_policy())
    if settings.llm_mode == "mock":
        return MockSarDrafter(template, gate=resolved_gate)
    sar_config = config or load_sar_llm_config(_config_anchored(settings.sar_config_file))
    stages = sar_config.stages(settings.sar_profile or None)
    resolved_budget = budget or BudgetGuard()
    resolved_cache = cache or InMemorySarDraftCache()
    resolved_client = client or LlmClient.from_settings()
    resolved_catalog = catalog or load_catalog(get_llm_settings().catalog_path)
    drafters = [
        LiveSarDrafter(
            client=resolved_client,
            catalog=resolved_catalog,
            prompt=template,
            model=stage.model,
            max_output_tokens=sar_config.max_output_tokens,
            gate=resolved_gate,
            reasoning_effort=sar_config.reasoning_effort,
            budget=resolved_budget,
            cache=resolved_cache,
            fallbacks=sar_config.fallbacks,
            task_type=TaskType.ANALYSIS,
            stage=stage.name,
            connection=stage.connection,
            constrained_decoding=stage.constrained_decoding,
        )
        for stage in stages
    ]
    if len(drafters) == 1:
        return drafters[0]
    return QualityGatedSarDrafter(
        tiers=tuple(
            SarCascadeTier(
                name=stage.name,
                drafter=drafter,
                requires_egress_class=stage.requires_egress_class,
            )
            for stage, drafter in zip(stages, drafters, strict=True)
        ),
        gate=resolved_gate,
        budget=resolved_budget,
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
    egress_policy = load_egress_policy()

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
            egress_policy=egress_policy,
        )
        graph = build_agent_graph(
            runtime=runtime,
            config=resolved_config,
            prompts=prompts,
            run_id=run_id,
            record_execution=record_execution,
            replay=replay,
            egress_policy=egress_policy,
        )
        return MultiAgentSarDrafter(
            graph=graph,
            config=resolved_config,
            prompts=prompts,
            gate=SarQualityGate(load_sar_gate_policy()),
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
