"""Summary: Frozen, fail-fast configuration for the bounded SAR agent team.
The loader rejects duplicate YAML keys, validates every provider reference and
capability against the LLM catalog, and requires every configured tool name to
exist in the registry supplied by the caller.

Key classes:
- AgentConfig: one role's model, prompt, tool, and output limits.
- AgentSetConfig: the required configuration for all four roles.
- AgentWorkflowConfig: workflow bounds and fallback policy.
- AgentQuotaConfig: live investigation quotas.
- AgentsConfig: complete immutable agent configuration.
- AgentsConfigError: safe configuration-load failure.

Key functions:
- load_agents_config: parse and validate the committed agent configuration.
- validate_agents_config: validate catalog capabilities and tool registry membership.

Notes:
- Tool registry names are injected because the tenant-scoped handlers land in Phase 3.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from fraudlens_backend.db.models.enums import AgentRole as _AgentRole
from fraudlens_backend.settings import find_config_dir
from fraudlens_llm import Catalog, ModelNotFoundError

AgentRole = _AgentRole


class AgentConfig(BaseModel):
    """One role's model routing, prompt, tool allowlist, and response limits."""

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    model: str = Field(..., min_length=1, description="Primary catalog model reference.")
    fallbacks: tuple[str, ...] = Field(
        default=(), description="Ordered catalog fallback references."
    )
    prompt_id: str = Field(..., min_length=1, description="Versioned prompt template id.")
    max_output_tokens: int = Field(..., gt=0, description="Maximum output tokens per model call.")
    max_tool_calls: int = Field(
        default=0, ge=0, description="Maximum tool invocations allowed for this role."
    )
    tools: tuple[str, ...] = Field(
        default=(), description="Exact tool-name allowlist for the role."
    )
    reasoning_effort: str | None = Field(
        default=None, min_length=1, description="Optional provider reasoning-effort hint."
    )
    top_k: int | None = Field(
        default=None, gt=0, description="Optional maximum regulatory matches to retain."
    )

    @model_validator(mode="after")
    def validate_tool_bound(self) -> AgentConfig:
        """Require a positive invocation bound whenever tools are enabled."""
        if self.tools and self.max_tool_calls == 0:
            raise ValueError("agents with tools require max_tool_calls greater than zero")
        if len(set(self.tools)) != len(self.tools):
            raise ValueError("agent tool allowlists may not contain duplicate names")
        return self


class AgentSetConfig(BaseModel):
    """The required configuration for every role in the bounded workflow."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_investigator: AgentConfig = Field(
        ..., description="Evidence-investigation role configuration."
    )
    regulatory_analyst: AgentConfig = Field(
        ..., description="Regulatory-analysis role configuration."
    )
    sar_writer: AgentConfig = Field(..., description="SAR-writing role configuration.")
    compliance_reviewer: AgentConfig = Field(
        ..., description="Compliance-review role configuration."
    )

    def items(self) -> Iterator[tuple[AgentRole, AgentConfig]]:
        """Yield every required role and its configuration in stable workflow order."""
        yield AgentRole.EVIDENCE_INVESTIGATOR, self.evidence_investigator
        yield AgentRole.REGULATORY_ANALYST, self.regulatory_analyst
        yield AgentRole.SAR_WRITER, self.sar_writer
        yield AgentRole.COMPLIANCE_REVIEWER, self.compliance_reviewer

    def for_role(self, role: AgentRole) -> AgentConfig:
        """Return the configuration bound to one stable role."""
        return cast(AgentConfig, getattr(self, role.value))


class AgentWorkflowConfig(BaseModel):
    """Hard workflow bounds and single-writer fallback policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_revisions: int = Field(..., ge=0, le=1, description="Maximum writer revision count.")
    parallel_investigation: bool = Field(
        ..., description="Whether evidence and regulatory analysis may proceed concurrently."
    )
    agent_timeout_s: float = Field(
        ..., gt=0, description="Per-agent wall-clock timeout in seconds."
    )
    workflow_timeout_s: float = Field(
        ..., gt=0, description="Whole-workflow wall-clock timeout in seconds."
    )
    max_cost_usd_per_investigation: Decimal = Field(
        ..., gt=0, description="Maximum worst-case provider cost per investigation."
    )
    fallback_to_single_writer: bool = Field(
        ..., description="Whether an unrecoverable team fault may use the live single writer."
    )

    @model_validator(mode="after")
    def validate_timeout_order(self) -> AgentWorkflowConfig:
        """Require the overall timeout to accommodate at least one agent call."""
        if self.workflow_timeout_s < self.agent_timeout_s:
            raise ValueError("workflow_timeout_s must be at least agent_timeout_s")
        return self


class AgentQuotaConfig(BaseModel):
    """Daily live-mode investigation quotas."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    live_runs_per_ip_per_day: int = Field(..., gt=0, description="Daily live quota per source IP.")
    live_runs_total_per_day: int = Field(
        ..., gt=0, description="Daily live quota across all users."
    )


class AgentsConfig(BaseModel):
    """Complete immutable configuration for the bounded four-agent workflow."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    graph_version: str = Field(..., min_length=1, description="Persisted agent graph version.")
    workflow: AgentWorkflowConfig = Field(..., description="Workflow bounds and fallback policy.")
    quotas: AgentQuotaConfig = Field(..., description="Live-mode usage quotas.")
    agents: AgentSetConfig = Field(..., description="Required four-role configuration.")


class AgentsConfigError(RuntimeError):
    """Raised when the agent configuration is unreadable or fails closed validation."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """YAML loader that rejects duplicate mapping keys instead of silently overwriting."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    """Construct one YAML mapping while rejecting duplicate keys."""
    loader.flatten_mapping(node)
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key ({key})",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_agents_config(
    *,
    catalog: Catalog,
    available_tools: Collection[str],
    path: Path | None = None,
) -> AgentsConfig:
    """Load agent YAML and fail fast on schema, catalog, capability, or tool drift."""
    config_path = path or (find_config_dir() / "llm" / "agents.yml")
    try:
        raw: Any = yaml.load(config_path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
        config = AgentsConfig.model_validate(raw or {})
        validate_agents_config(config, catalog=catalog, available_tools=available_tools)
    except (OSError, yaml.YAMLError, ValidationError, ValueError) as exc:
        raise AgentsConfigError(f"Agent configuration is invalid: {config_path}") from exc
    return config


def validate_agents_config(
    config: AgentsConfig,
    *,
    catalog: Catalog,
    available_tools: Collection[str],
) -> None:
    """Validate role model capabilities, pricing, and tool registry membership."""
    registered_tools = set(available_tools)
    for role, agent in config.agents.items():
        missing_tools = set(agent.tools) - registered_tools
        if missing_tools:
            missing = ", ".join(sorted(missing_tools))
            raise ValueError(f"agents.{role.value} references unavailable tools: {missing}")
        for ref in (agent.model, *agent.fallbacks):
            try:
                _provider, _model_id, card = catalog.get(ref)
            except ModelNotFoundError as exc:
                raise ValueError(f"agents.{role.value} references an unknown model") from exc
            if not card.callable:
                raise ValueError(f"agents.{role.value} references a non-callable model")
            if not card.structured_output:
                raise ValueError(f"agents.{role.value} requires structured output")
            if agent.tools and not card.tool_calling:
                raise ValueError(f"agents.{role.value} requires tool calling")
            if card.pricing_basis != "per_million_tokens" or card.output_price_per_million is None:
                raise ValueError(f"agents.{role.value} requires verified output-token pricing")
