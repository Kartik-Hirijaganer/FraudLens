"""Summary: Internal resolved-model and guarded-request contracts for LlmClient.

Key classes:
- ResolvedModel: resolved catalog model and provider configuration.
- InputGuardrails: masked messages plus their guardrail report.
- PreparedGeneration: provider-ready guarded generation request.

Key functions:
- resolve_model: join a catalog model to its provider configuration.
- require_kind: enforce callable status and model kind.
- require_generation_capabilities: enforce requested chat capabilities.
- enforce_provider_policy:
- eligible_fallbacks: retain only governance-compatible fallback models.

Notes:
- These internal Pydantic boundaries fail closed on unexpected fields.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fraudlens_llm.catalog import Catalog, Kind, ModelCard
from fraudlens_llm.exceptions import CapabilityMismatchError, PolicyError
from fraudlens_llm.models import (
    DataClass,
    GuardrailReport,
    LlmMessage,
    ToolDefinition,
)
from fraudlens_llm.providers import (
    ProviderConfig,
    Providers,
    allows_data_class,
    is_equal_or_stricter,
)


class ResolvedModel(BaseModel):
    """Internal resolved model, card, and provider config."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: str = Field(..., description="Provider/model reference.")
    provider: str = Field(..., description="Provider name.")
    model_id: str = Field(..., description="Provider-native model id.")
    card: ModelCard = Field(..., description="Catalog model card.")
    provider_config: ProviderConfig = Field(..., description="Provider config.")


class InputGuardrails(BaseModel):
    """Internal result of input guardrails."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    messages: list[LlmMessage] = Field(..., description="Masked provider messages.")
    report: GuardrailReport = Field(..., description="Input guardrail report.")


class PreparedGeneration(BaseModel):
    """Internal provider-ready generation request after input guardrails."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    resolved: ResolvedModel = Field(..., description="Resolved primary model.")
    messages: list[LlmMessage] = Field(..., description="Masked provider messages.")
    report: GuardrailReport = Field(..., description="Input guardrail report.")
    data_class: DataClass = Field(..., description="Governed data classification.")
    tools: tuple[ToolDefinition, ...] = Field(..., description="Validated tool definitions.")
    tool_choice: str | None = Field(default=None, description="Validated tool selection policy.")
    response_schema: dict[str, Any] | None = Field(
        default=None,
        description="Validated strict structured-output schema.",
    )


def resolve_model(catalog: Catalog, providers: Providers, ref: str) -> ResolvedModel:
    """Resolve a catalog reference plus provider configuration."""
    provider, model_id, card = catalog.get(ref)
    return ResolvedModel(
        ref=ref,
        provider=provider,
        model_id=model_id,
        card=card,
        provider_config=providers.get(provider),
    )


def require_kind(resolved: ResolvedModel, kind: Kind) -> None:
    """Validate callable status and model kind."""
    if not resolved.card.callable:
        raise CapabilityMismatchError(f"Model '{resolved.ref}' is not callable in v1")
    if resolved.card.kind != kind:
        raise CapabilityMismatchError(f"Model '{resolved.ref}' is not a {kind.value} model")


def require_generation_capabilities(
    resolved: ResolvedModel,
    *,
    tools: Sequence[ToolDefinition],
    response_schema: dict[str, Any] | None,
) -> None:
    """Fail before provider access when a model lacks requested capabilities."""
    if tools and not resolved.card.tool_calling:
        raise CapabilityMismatchError(
            f"Model '{resolved.ref}' does not support native tool calling"
        )
    if response_schema is not None and not resolved.card.structured_output:
        raise CapabilityMismatchError(f"Model '{resolved.ref}' does not support structured output")


def enforce_provider_policy(resolved: ResolvedModel, data_class: DataClass) -> None:
    """Fail closed if the primary provider disallows the call data class."""
    if not allows_data_class(resolved.provider_config, data_class):
        raise PolicyError(
            f"Provider '{resolved.provider}' does not allow data class '{data_class.value}'"
        )


def eligible_fallbacks(  # noqa: PLR0913
    catalog: Catalog,
    providers: Providers,
    primary: ResolvedModel,
    data_class: DataClass,
    fallback_refs: Sequence[str],
    *,
    tools: Sequence[ToolDefinition],
    response_schema: dict[str, Any] | None,
    allow_policy_downgrade: bool,
) -> list[ResolvedModel]:
    """Return fallbacks that do not weaken provider governance posture."""
    eligible: list[ResolvedModel] = []
    for ref in fallback_refs:
        candidate = resolve_model(catalog, providers, ref)
        require_kind(candidate, Kind.CHAT)
        require_generation_capabilities(candidate, tools=tools, response_schema=response_schema)
        if not allows_data_class(candidate.provider_config, data_class):
            continue
        if not allow_policy_downgrade and not is_equal_or_stricter(
            primary.provider_config, candidate.provider_config
        ):
            continue
        eligible.append(candidate)
    return eligible
