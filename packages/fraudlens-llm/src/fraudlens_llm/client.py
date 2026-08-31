"""Summary: Public guardrailed async LLM client. It is the first exported call path
that can reach provider SDKs and enforces compliance policy, PHI masking, prompt
risk scanning, output scanning/sanitization, safe logging, and fallback governance.
Chat generation supports both blocking and provider-native streaming transport; the
streaming path buffers raw deltas until the complete output passes the same guardrails.
Empty provider generations are retryable failures so an eligible governed fallback can serve them.

Key classes:
- BoundModel: Convenience wrapper bound to one provider/model reference.
- StreamGenerationRequest: Typed request for provider-native guarded generation.
- LlmClient: Catalog-driven guardrailed async client.

Key functions:
- (none)

Notes:
- Private adapters are never exported from fraudlens_llm.__all__.
- Undeclared tool calls fail closed unless a caller explicitly captures them for audit/refusal.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping, Sequence
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from fraudlens_llm.adapters.anthropic import AnthropicAdapter
from fraudlens_llm.adapters.base import (
    AdapterGenerateResult,
    ProviderAdapter,
    StreamingProviderAdapter,
)
from fraudlens_llm.adapters.openai_compatible import OpenAiCompatibleAdapter
from fraudlens_llm.catalog import Catalog, GenerationParams, Kind, ModelCard, load_catalog
from fraudlens_llm.exceptions import (
    CapabilityMismatchError,
    GuardrailError,
    LlmError,
    PolicyError,
    ProviderError,
)
from fraudlens_llm.models import (
    DataClass,
    EmbeddingResult,
    GuardrailDecision,
    GuardrailReport,
    LlmMessage,
    LlmResult,
    LlmUsage,
    MaskingReport,
    ScanOutcome,
    TaskType,
    ToolCall,
    ToolDefinition,
)
from fraudlens_llm.providers import (
    Protocol,
    ProviderConfig,
    Providers,
    allows_data_class,
    is_equal_or_stricter,
    load_providers,
)
from fraudlens_llm.security.output import sanitize_output
from fraudlens_llm.security.phi import mask_texts
from fraudlens_llm.security.phishing import scan_output_risk
from fraudlens_llm.security.policy import policy_outcome, system_policy_message
from fraudlens_llm.security.prompt_risk import scan_prompt_risk
from fraudlens_llm.security.redaction import safe_log_event
from fraudlens_llm.security.tools import (
    validate_response_schema,
    validate_tool_calls,
    validate_tool_definitions,
)
from fraudlens_llm.settings import LlmSettings, get_llm_settings

_LOGGER = logging.getLogger(__name__)
_MESSAGE_ADAPTER: TypeAdapter[list[LlmMessage]] = TypeAdapter(list[LlmMessage])
_TOKENS_PER_MILLION = 1_000_000
_NOT_APPLICABLE = ScanOutcome(decision=GuardrailDecision.NOT_APPLICABLE, findings=[])


class _ResolvedModel(BaseModel):
    """Internal resolved model, card, and provider config."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: str = Field(..., description="Provider/model reference.")
    provider: str = Field(..., description="Provider name.")
    model_id: str = Field(..., description="Provider-native model id.")
    card: ModelCard = Field(..., description="Catalog model card.")
    provider_config: ProviderConfig = Field(..., description="Provider config.")


class _InputGuardrails(BaseModel):
    """Internal result of input guardrails."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    messages: list[LlmMessage] = Field(..., description="Masked provider messages.")
    report: GuardrailReport = Field(..., description="Input guardrail report.")


class _PreparedGeneration(BaseModel):
    """Internal provider-ready generation request after input guardrails."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    resolved: _ResolvedModel = Field(..., description="Resolved primary model.")
    messages: list[LlmMessage] = Field(..., description="Masked provider messages.")
    report: GuardrailReport = Field(..., description="Input guardrail report.")
    data_class: DataClass = Field(..., description="Governed data classification.")
    tools: tuple[ToolDefinition, ...] = Field(..., description="Validated tool definitions.")
    tool_choice: str | None = Field(default=None, description="Validated tool selection policy.")
    response_schema: dict[str, Any] | None = Field(
        default=None,
        description="Validated strict structured-output schema.",
    )


class StreamGenerationRequest(BaseModel):
    """Typed request for a provider-native stream assembled behind output guardrails."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    messages: Sequence[LlmMessage] = Field(
        ..., description="Chat messages validated and masked before the provider call."
    )
    model: str | None = Field(default=None, description="Provider/model reference override.")
    overrides: GenerationParams | None = Field(
        default=None, description="Per-call generation parameter overrides."
    )
    task_type: TaskType = Field(
        default=TaskType.GENERATION, description="Guardrail task classification."
    )
    data_class: DataClass | None = Field(
        default=None, description="Provider-governance data classification override."
    )
    include_raw: bool = Field(
        default=False, description="Whether non-production policy may return raw output."
    )
    fallbacks: tuple[str, ...] = Field(
        default=(), description="Ordered governance-eligible fallback model references."
    )


class BoundModel:
    """Convenience wrapper bound to one provider/model reference."""

    def __init__(self, client: LlmClient, ref: str) -> None:
        """Create a bound model wrapper."""
        self._client = client
        self._ref = ref

    async def generate(  # noqa: PLR0913 - mirrors LlmClient.generate for bound models.
        self,
        messages: Sequence[LlmMessage | dict[str, object]],
        *,
        overrides: GenerationParams | None = None,
        task_type: TaskType = TaskType.GENERATION,
        data_class: DataClass | None = None,
        include_raw: bool = False,
        fallbacks: Sequence[str] | None = None,
        tools: Sequence[ToolDefinition] | None = None,
        tool_choice: str | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> LlmResult:
        """Generate text with this bound model."""
        return await self._client.generate(
            messages,
            model=self._ref,
            overrides=overrides,
            task_type=task_type,
            data_class=data_class,
            include_raw=include_raw,
            fallbacks=fallbacks,
            tools=tools,
            tool_choice=tool_choice,
            response_schema=response_schema,
        )

    async def embed(
        self,
        inputs: Sequence[str],
        *,
        overrides: GenerationParams | None = None,
        data_class: DataClass | None = None,
    ) -> EmbeddingResult:
        """Generate embeddings with this bound model."""
        return await self._client.embed(
            inputs,
            model=self._ref,
            overrides=overrides,
            data_class=data_class,
        )


class LlmClient:
    """Catalog-driven guardrailed async LLM client."""

    def __init__(
        self,
        *,
        catalog: Catalog,
        providers: Providers,
        settings: LlmSettings | None = None,
    ) -> None:
        """Create a client from validated catalog, providers, and settings."""
        self._catalog = catalog
        self._providers = providers
        self._settings = settings or get_llm_settings()
        self._adapters: dict[str, ProviderAdapter] = {}

    @classmethod
    def from_config(
        cls,
        catalog: Catalog,
        providers: Providers,
        settings: LlmSettings | None = None,
    ) -> LlmClient:
        """Create a client from validated config objects."""
        return cls(catalog=catalog, providers=providers, settings=settings)

    @classmethod
    def from_settings(cls, settings: LlmSettings | None = None) -> LlmClient:
        """Load configured catalog/providers from settings and create a client."""
        resolved_settings = settings or get_llm_settings()
        return cls(
            catalog=load_catalog(resolved_settings.catalog_path),
            providers=load_providers(resolved_settings.providers_path),
            settings=resolved_settings,
        )

    async def generate(  # noqa: PLR0913 - public API shape is defined by the plan.
        self,
        messages: Sequence[LlmMessage | dict[str, object]],
        *,
        model: str | None = None,
        overrides: GenerationParams | None = None,
        task_type: TaskType = TaskType.GENERATION,
        data_class: DataClass | None = None,
        include_raw: bool = False,
        capture_undeclared_tool_calls: bool = False,
        fallbacks: Sequence[str] | None = None,
        tools: Sequence[ToolDefinition] | None = None,
        tool_choice: str | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> LlmResult:
        """Generate chat text through the guardrailed pipeline."""
        prepared = self._prepare_generation(
            messages,
            model=model,
            task_type=task_type,
            data_class=data_class,
            tools=tools,
            tool_choice=tool_choice,
            response_schema=response_schema,
            capture_undeclared_tool_calls=capture_undeclared_tool_calls,
        )
        return await self._generate_with_fallbacks(
            prepared.resolved,
            prepared.messages,
            prepared.report,
            overrides=overrides,
            task_type=task_type,
            data_class=prepared.data_class,
            include_raw=include_raw,
            fallbacks=fallbacks or (),
            native_stream=False,
            tools=prepared.tools,
            tool_choice=prepared.tool_choice,
            response_schema=prepared.response_schema,
            capture_undeclared_tool_calls=capture_undeclared_tool_calls,
        )

    async def generate_stream(self, request: StreamGenerationRequest) -> LlmResult:
        """Consume a provider-native stream, then scan and return its assembled output."""
        prepared = self._prepare_generation(
            request.messages,
            model=request.model,
            task_type=request.task_type,
            data_class=request.data_class,
            tools=None,
            tool_choice=None,
            response_schema=None,
            capture_undeclared_tool_calls=False,
        )
        return await self._generate_with_fallbacks(
            prepared.resolved,
            prepared.messages,
            prepared.report,
            overrides=request.overrides,
            task_type=request.task_type,
            data_class=prepared.data_class,
            include_raw=request.include_raw,
            fallbacks=request.fallbacks,
            native_stream=True,
            tools=prepared.tools,
            tool_choice=prepared.tool_choice,
            response_schema=prepared.response_schema,
            capture_undeclared_tool_calls=False,
        )

    async def embed(
        self,
        inputs: Sequence[str],
        *,
        model: str,
        overrides: GenerationParams | None = None,
        data_class: DataClass | None = None,
    ) -> EmbeddingResult:
        """Generate embeddings through the guardrailed pipeline."""
        resolved = self._resolve_model(model)
        self._require_kind(resolved, Kind.EMBED)
        if resolved.provider_config.protocol != Protocol.OPENAI_COMPATIBLE:
            raise CapabilityMismatchError("Embeddings require an openai_compatible provider")
        resolved_data_class = data_class or self._settings.default_data_class
        self._enforce_provider_policy(resolved, resolved_data_class)
        masked_inputs, masking_report = _mask_inputs(inputs, self._settings)
        guardrail = _embedding_guardrail_report(
            settings=self._settings,
            masking_report=masking_report,
            policy=policy_outcome(allowed=True),
        )
        params = _merge_params(None, resolved.card.default_params, overrides)
        start = time.perf_counter()
        result = await self._adapter_for(resolved).embed(
            model_id=resolved.model_id,
            card=resolved.card,
            inputs=masked_inputs,
            params=params,
        )
        _safe_log_success(
            resolved=resolved,
            data_class=resolved_data_class,
            usage=result.usage,
            guardrail=guardrail,
            start=start,
            fallback_count=0,
        )
        return EmbeddingResult(
            embeddings=result.embeddings,
            model=resolved.ref,
            provider=resolved.provider,
            usage=result.usage,
            guardrail=guardrail,
        )

    def get_model(self, ref: str) -> BoundModel:
        """Return a bound model wrapper after resolving provider configuration."""
        self._resolve_model(ref)
        return BoundModel(self, ref)

    def _prepare_generation(  # noqa: PLR0913 - keeps guarded request fields explicit.
        self,
        messages: Sequence[LlmMessage | dict[str, object]],
        *,
        model: str | None,
        task_type: TaskType,
        data_class: DataClass | None,
        tools: Sequence[ToolDefinition] | None,
        tool_choice: str | None,
        response_schema: dict[str, Any] | None,
        capture_undeclared_tool_calls: bool,
    ) -> _PreparedGeneration:
        """Resolve policy and input guardrails once for blocking or streaming transport."""
        resolved = self._resolve_model(model or self._settings.default_model)
        self._require_kind(resolved, Kind.CHAT)
        resolved_data_class = data_class or self._settings.default_data_class
        self._enforce_provider_policy(resolved, resolved_data_class)
        resolved_tools = tuple(tools or ())
        validate_tool_definitions(resolved_tools, tool_choice=tool_choice)
        validate_response_schema(response_schema)
        self._require_generation_capabilities(
            resolved,
            tools=resolved_tools,
            response_schema=response_schema,
        )
        input_guardrails = self._run_input_guardrails(
            _coerce_messages(messages),
            task_type=task_type,
            data_class=resolved_data_class,
            tools=resolved_tools,
            capture_undeclared_tool_calls=capture_undeclared_tool_calls,
        )
        return _PreparedGeneration(
            resolved=resolved,
            messages=[system_policy_message(), *input_guardrails.messages],
            report=input_guardrails.report,
            data_class=resolved_data_class,
            tools=resolved_tools,
            tool_choice=tool_choice,
            response_schema=response_schema,
        )

    async def _generate_with_fallbacks(  # noqa: PLR0913 - keeps routing context explicit.
        self,
        resolved: _ResolvedModel,
        messages: Sequence[LlmMessage],
        guardrail: GuardrailReport,
        *,
        overrides: GenerationParams | None,
        task_type: TaskType,
        data_class: DataClass,
        include_raw: bool,
        fallbacks: Sequence[str],
        native_stream: bool,
        tools: Sequence[ToolDefinition],
        tool_choice: str | None,
        response_schema: dict[str, Any] | None,
        capture_undeclared_tool_calls: bool,
    ) -> LlmResult:
        """Generate through one guarded fallback pipeline using blocking or native transport."""
        last_error: LlmError | None = None
        eligible = [
            resolved,
            *self._eligible_fallbacks(
                resolved,
                data_class,
                fallbacks,
                tools=tools,
                response_schema=response_schema,
            ),
        ]
        for fallback_count, target in enumerate(eligible):
            params = _merge_params(
                self._settings.default_params,
                target.card.default_params,
                overrides,
            )
            start = time.perf_counter()
            try:
                adapter_result = await self._invoke_generation(
                    target,
                    messages,
                    params,
                    native_stream=native_stream,
                    tools=tools,
                    tool_choice=tool_choice,
                    response_schema=response_schema,
                )
                if not adapter_result.text.strip() and not adapter_result.tool_calls:
                    raise ProviderError(
                        "LLM provider returned an empty generation",
                        retryable=True,
                    )
            except LlmError as exc:
                if exc.retryable:
                    last_error = exc
                    continue
                raise
            return _generation_result(
                target=target,
                adapter_result=adapter_result,
                guardrail=guardrail,
                settings=self._settings,
                task_type=task_type,
                data_class=data_class,
                include_raw=include_raw,
                start=start,
                fallback_count=fallback_count,
                tools=tools,
                capture_undeclared_tool_calls=capture_undeclared_tool_calls,
            )
        if last_error is not None:
            raise last_error
        raise PolicyError("No fallback model satisfied provider governance policy")

    async def _invoke_generation(  # noqa: PLR0913 - mirrors adapter capability arguments.
        self,
        target: _ResolvedModel,
        messages: Sequence[LlmMessage],
        params: GenerationParams,
        *,
        native_stream: bool,
        tools: Sequence[ToolDefinition],
        tool_choice: str | None,
        response_schema: dict[str, Any] | None,
    ) -> AdapterGenerateResult:
        """Invoke the selected transport and normalize it to one adapter result."""
        if native_stream:
            return await _collect_adapter_stream(
                self._streaming_adapter_for(target),
                model_id=target.model_id,
                card=target.card,
                messages=messages,
                params=params,
            )
        return await self._adapter_for(target).generate(
            model_id=target.model_id,
            card=target.card,
            messages=messages,
            params=params,
            tools=tools,
            tool_choice=tool_choice,
            response_schema=response_schema,
        )

    def _resolve_model(self, ref: str) -> _ResolvedModel:
        """Resolve a catalog ref plus provider config."""
        provider, model_id, card = self._catalog.get(ref)
        provider_config = self._providers.get(provider)
        return _ResolvedModel(
            ref=ref,
            provider=provider,
            model_id=model_id,
            card=card,
            provider_config=provider_config,
        )

    def _require_kind(self, resolved: _ResolvedModel, kind: Kind) -> None:
        """Validate callable status and model kind."""
        if not resolved.card.callable:
            raise CapabilityMismatchError(f"Model '{resolved.ref}' is not callable in v1")
        if resolved.card.kind != kind:
            raise CapabilityMismatchError(f"Model '{resolved.ref}' is not a {kind.value} model")

    def _require_generation_capabilities(
        self,
        resolved: _ResolvedModel,
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
            raise CapabilityMismatchError(
                f"Model '{resolved.ref}' does not support structured output"
            )

    def _enforce_provider_policy(self, resolved: _ResolvedModel, data_class: DataClass) -> None:
        """Fail closed if the primary provider disallows the call data class."""
        if not allows_data_class(resolved.provider_config, data_class):
            raise PolicyError(
                f"Provider '{resolved.provider}' does not allow data class '{data_class.value}'"
            )

    def _run_input_guardrails(
        self,
        messages: Sequence[LlmMessage],
        *,
        task_type: TaskType,
        data_class: DataClass,
        tools: Sequence[ToolDefinition],
        capture_undeclared_tool_calls: bool,
    ) -> _InputGuardrails:
        """Run PHI masking and prompt-risk checks before any adapter call."""
        _ = data_class
        for message in messages:
            _validate_tool_calls_for_capture(
                message.tool_calls,
                tools,
                capture_undeclared_tool_calls=capture_undeclared_tool_calls,
            )
        masked_messages, masked_texts, masking_report = _mask_messages(messages, self._settings)
        prompt_risk = scan_prompt_risk(
            "\n".join(masked_texts),
            strictness=self._settings.guardrail_strictness,
            task_type=task_type,
        )
        if prompt_risk.decision == GuardrailDecision.BLOCK:
            raise GuardrailError("Input guardrails blocked the LLM request")
        report = _generation_guardrail_report(
            settings=self._settings,
            masking_report=masking_report,
            prompt_risk=prompt_risk,
            output=_NOT_APPLICABLE,
            phishing=_NOT_APPLICABLE,
            policy=policy_outcome(allowed=True),
        )
        return _InputGuardrails(messages=masked_messages, report=report)

    def _adapter_for(self, resolved: _ResolvedModel) -> ProviderAdapter:
        """Return or create the private adapter for a provider."""
        adapter = self._adapters.get(resolved.provider)
        if adapter is not None:
            return adapter
        if resolved.provider_config.protocol == Protocol.OPENAI_COMPATIBLE:
            adapter = OpenAiCompatibleAdapter(resolved.provider, resolved.provider_config)
        elif resolved.provider_config.protocol == Protocol.ANTHROPIC:
            adapter = AnthropicAdapter(resolved.provider, resolved.provider_config)
        else:
            raise CapabilityMismatchError(f"Unsupported provider protocol for {resolved.provider}")
        self._adapters[resolved.provider] = adapter
        return adapter

    def _streaming_adapter_for(self, resolved: _ResolvedModel) -> StreamingProviderAdapter:
        """Return the native streaming adapter supported by OpenAI-compatible providers."""
        if resolved.provider_config.protocol != Protocol.OPENAI_COMPATIBLE:
            raise CapabilityMismatchError(
                "Native chat streaming requires an openai_compatible provider"
            )
        return cast(StreamingProviderAdapter, self._adapter_for(resolved))

    def _eligible_fallbacks(
        self,
        primary: _ResolvedModel,
        data_class: DataClass,
        fallback_refs: Sequence[str],
        *,
        tools: Sequence[ToolDefinition],
        response_schema: dict[str, Any] | None,
    ) -> list[_ResolvedModel]:
        """Return fallbacks that do not weaken provider governance posture."""
        eligible: list[_ResolvedModel] = []
        for ref in fallback_refs:
            candidate = self._resolve_model(ref)
            self._require_kind(candidate, Kind.CHAT)
            self._require_generation_capabilities(
                candidate,
                tools=tools,
                response_schema=response_schema,
            )
            if not allows_data_class(candidate.provider_config, data_class):
                continue
            if not self._settings.allow_policy_downgrade and not is_equal_or_stricter(
                primary.provider_config, candidate.provider_config
            ):
                continue
            eligible.append(candidate)
        return eligible


def _coerce_messages(messages: Sequence[LlmMessage | dict[str, object]]) -> list[LlmMessage]:
    """Validate message inputs into LlmMessage instances."""
    return _MESSAGE_ADAPTER.validate_python(list(messages))


def _mask_messages(
    messages: Sequence[LlmMessage],
    settings: LlmSettings,
) -> tuple[list[LlmMessage], list[str], MaskingReport]:
    """Mask message text and serialized tool arguments as one guarded input surface."""
    raw_texts: list[str] = []
    content_positions: dict[int, int] = {}
    argument_positions: dict[tuple[int, int], int] = {}
    for message_index, message in enumerate(messages):
        if message.content is not None:
            content_positions[message_index] = len(raw_texts)
            raw_texts.append(message.content)
        for call_index, tool_call in enumerate(message.tool_calls):
            argument_positions[(message_index, call_index)] = len(raw_texts)
            raw_texts.append(_serialize_arguments(tool_call.arguments))

    masked_texts, masking_report = _mask_inputs(raw_texts, settings)
    masked_messages: list[LlmMessage] = []
    for message_index, message in enumerate(messages):
        content_position = content_positions.get(message_index)
        masked_calls = tuple(
            tool_call.model_copy(
                update={
                    "arguments": _deserialize_arguments(
                        masked_texts[argument_positions[(message_index, call_index)]]
                    )
                }
            )
            for call_index, tool_call in enumerate(message.tool_calls)
        )
        masked_messages.append(
            message.model_copy(
                update={
                    "content": (
                        masked_texts[content_position] if content_position is not None else None
                    ),
                    "tool_calls": masked_calls,
                }
            )
        )
    return masked_messages, masked_texts, masking_report


def _mask_tool_calls(
    tool_calls: Sequence[ToolCall],
    settings: LlmSettings,
) -> tuple[tuple[ToolCall, ...], list[str], MaskingReport]:
    """Mask provider-generated tool arguments before returning them to callers."""
    serialized = [_serialize_arguments(tool_call.arguments) for tool_call in tool_calls]
    masked_texts, report = _mask_inputs(serialized, settings)
    masked_calls = tuple(
        tool_call.model_copy(update={"arguments": _deserialize_arguments(masked_text)})
        for tool_call, masked_text in zip(tool_calls, masked_texts, strict=True)
    )
    return masked_calls, masked_texts, report


def _serialize_arguments(arguments: Mapping[str, object]) -> str:
    """Serialize tool arguments deterministically for guardrail scanning."""
    return json.dumps(arguments, sort_keys=True, separators=(",", ":"))


def _deserialize_arguments(serialized: str) -> dict[str, object]:
    """Restore masked tool arguments while failing closed on invalid JSON."""
    try:
        arguments = json.loads(serialized)
    except json.JSONDecodeError as exc:
        raise GuardrailError("Masked tool arguments were not valid JSON") from exc
    if not isinstance(arguments, dict):
        raise GuardrailError("Tool arguments must be a JSON object")
    return arguments


async def _collect_adapter_stream(
    adapter: StreamingProviderAdapter,
    *,
    model_id: str,
    card: ModelCard,
    messages: Sequence[LlmMessage],
    params: GenerationParams,
) -> AdapterGenerateResult:
    """Assemble native provider deltas into the normalized result guardrails consume."""
    text_parts: list[str] = []
    served_model: str | None = None
    finish_reason: str | None = None
    usage = LlmUsage()
    async for chunk in adapter.generate_stream(
        model_id=model_id,
        card=card,
        messages=messages,
        params=params,
    ):
        text_parts.append(chunk.text_delta)
        served_model = chunk.served_model or served_model
        finish_reason = chunk.finish_reason or finish_reason
        usage = chunk.usage or usage
    return AdapterGenerateResult(
        text="".join(text_parts),
        served_model=served_model,
        finish_reason=finish_reason,
        usage=usage,
    )


def _mask_inputs(
    inputs: Sequence[str],
    settings: LlmSettings,
) -> tuple[list[str], MaskingReport]:
    """Mask inputs and fail closed if local masking raises."""
    try:
        return mask_texts(inputs, settings.phi_masking_mode)
    except Exception as exc:
        raise GuardrailError("PHI masking failed closed") from exc


def _merge_params(
    defaults: GenerationParams | None,
    card_params: GenerationParams,
    overrides: GenerationParams | None,
) -> GenerationParams:
    """Merge params with precedence overrides > card > defaults."""
    data: dict[str, object] = {}
    if defaults is not None:
        data.update(defaults.model_dump(exclude_none=True))
    data.update(card_params.model_dump(exclude_none=True))
    if overrides is not None:
        data.update(overrides.model_dump(exclude_none=True))
    return GenerationParams.model_validate(data)


def _generation_result(  # noqa: PLR0913 - assembles result from explicit pipeline stages.
    *,
    target: _ResolvedModel,
    adapter_result: AdapterGenerateResult,
    guardrail: GuardrailReport,
    settings: LlmSettings,
    task_type: TaskType,
    data_class: DataClass,
    include_raw: bool,
    start: float,
    fallback_count: int,
    tools: Sequence[ToolDefinition],
    capture_undeclared_tool_calls: bool,
) -> LlmResult:
    """Scan raw output, sanitize it, safe-log success, and build public result."""
    _validate_tool_calls_for_capture(
        adapter_result.tool_calls,
        tools,
        capture_undeclared_tool_calls=capture_undeclared_tool_calls,
    )
    safe_tool_calls, tool_argument_texts, tool_masking = _mask_tool_calls(
        adapter_result.tool_calls,
        settings,
    )
    tool_prompt_risk = scan_prompt_risk(
        "\n".join(tool_argument_texts),
        strictness=settings.guardrail_strictness,
        task_type=task_type,
    )
    if tool_prompt_risk.decision == GuardrailDecision.BLOCK:
        raise GuardrailError("Tool argument guardrails blocked the LLM response")
    output, phishing = scan_output_risk(
        adapter_result.text,
        strictness=settings.guardrail_strictness,
        task_type=task_type,
    )
    if GuardrailDecision.BLOCK in {output.decision, phishing.decision}:
        raise GuardrailError("Output guardrails blocked the LLM response")
    final_guardrail = _generation_guardrail_report(
        settings=settings,
        masking_report=_combine_masking_reports(guardrail.masking, tool_masking),
        prompt_risk=_combine_scan_outcomes(guardrail.prompt_risk, tool_prompt_risk),
        output=output,
        phishing=phishing,
        policy=guardrail.policy,
    )
    result = LlmResult(
        safe_text=sanitize_output(adapter_result.text),
        raw_text=adapter_result.text if settings.allow_raw_output and include_raw else None,
        model=target.ref,
        provider=target.provider,
        served_model=adapter_result.served_model,
        finish_reason=adapter_result.finish_reason,
        usage=adapter_result.usage,
        tool_calls=safe_tool_calls,
        guardrail=final_guardrail,
    )
    _safe_log_success(
        resolved=target,
        data_class=data_class,
        usage=adapter_result.usage,
        guardrail=final_guardrail,
        start=start,
        fallback_count=fallback_count,
    )
    return result


def _validate_tool_calls_for_capture(
    tool_calls: Sequence[ToolCall],
    tools: Sequence[ToolDefinition],
    *,
    capture_undeclared_tool_calls: bool,
) -> None:
    """Validate executable calls while optionally retaining undeclared calls for refusal."""
    if not capture_undeclared_tool_calls:
        validate_tool_calls(tool_calls, tools)
        return
    declared_names = {tool.name for tool in tools}
    validate_tool_calls(
        tuple(tool_call for tool_call in tool_calls if tool_call.name in declared_names),
        tools,
    )


def _combine_masking_reports(first: MaskingReport, second: MaskingReport) -> MaskingReport:
    """Combine counts-only masking reports from input and tool-output surfaces."""
    counts = dict(first.counts)
    for category, count in second.counts.items():
        counts[category] = counts.get(category, 0) + count
    return MaskingReport(
        mode=first.mode,
        counts=dict(sorted(counts.items())),
        total_masked=sum(counts.values()),
    )


def _combine_scan_outcomes(first: ScanOutcome, second: ScanOutcome) -> ScanOutcome:
    """Combine prompt-risk outcomes while preserving counts-only findings."""
    decision = _overall_decision([first, second])
    return ScanOutcome(decision=decision, findings=[*first.findings, *second.findings])


def _generation_guardrail_report(  # noqa: PLR0913 - mirrors GuardrailReport stages.
    *,
    settings: LlmSettings,
    masking_report: MaskingReport,
    prompt_risk: ScanOutcome,
    output: ScanOutcome,
    phishing: ScanOutcome,
    policy: ScanOutcome,
) -> GuardrailReport:
    """Build a generation guardrail report from stage outcomes."""
    decision = _overall_decision([prompt_risk, output, phishing, policy])
    return GuardrailReport(
        decision=decision,
        strictness=settings.guardrail_strictness,
        masking=masking_report,
        prompt_risk=prompt_risk,
        output=output,
        phishing=phishing,
        policy=policy,
    )


def _embedding_guardrail_report(
    *,
    settings: LlmSettings,
    masking_report: MaskingReport,
    policy: ScanOutcome,
) -> GuardrailReport:
    """Build an embedding guardrail report with output stages marked not applicable."""
    return _generation_guardrail_report(
        settings=settings,
        masking_report=masking_report,
        prompt_risk=_NOT_APPLICABLE,
        output=_NOT_APPLICABLE,
        phishing=_NOT_APPLICABLE,
        policy=policy,
    )


def _overall_decision(outcomes: Sequence[ScanOutcome]) -> GuardrailDecision:
    """Return the strictest overall decision across guardrail outcomes."""
    decisions = {outcome.decision for outcome in outcomes}
    if GuardrailDecision.BLOCK in decisions:
        return GuardrailDecision.BLOCK
    if GuardrailDecision.FLAG in decisions:
        return GuardrailDecision.FLAG
    return GuardrailDecision.ALLOW


def _safe_log_success(  # noqa: PLR0913 - safe logging has an explicit allowlist.
    *,
    resolved: _ResolvedModel,
    data_class: DataClass,
    usage: LlmUsage,
    guardrail: GuardrailReport,
    start: float,
    fallback_count: int,
) -> None:
    """Emit a safe allowlisted success log payload."""
    latency_ms = int((time.perf_counter() - start) * 1000)
    _LOGGER.info(
        "llm_call",
        extra={
            "llm": safe_log_event(
                model=resolved.ref,
                provider=resolved.provider,
                data_class=data_class,
                status="success",
                usage=usage,
                guardrail_decision=guardrail.decision,
                policy_decision=guardrail.policy.decision,
                latency_ms=latency_ms,
                estimated_cost_usd=_estimate_cost(resolved.card, usage),
                fallback_count=fallback_count,
            )
        },
    )


def _estimate_cost(card: ModelCard, usage: LlmUsage) -> float | None:
    """Estimate token cost when verified token pricing is available."""
    if card.pricing_basis != "per_million_tokens":
        return None
    if card.input_price_per_million is None and card.output_price_per_million is None:
        return None
    input_cost = usage.input_tokens * (card.input_price_per_million or 0) / _TOKENS_PER_MILLION
    output_cost = usage.output_tokens * (card.output_price_per_million or 0) / _TOKENS_PER_MILLION
    return input_cost + output_cost
