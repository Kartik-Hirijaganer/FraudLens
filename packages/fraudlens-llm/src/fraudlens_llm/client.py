"""Summary: Public guardrailed async LLM client. It is the first exported call path
that can reach provider SDKs and enforces compliance policy, PHI masking, prompt
risk scanning, output scanning/sanitization, safe logging, and fallback governance.
Chat generation supports both blocking and provider-native streaming transport; the
streaming path buffers raw deltas until the complete output passes the same guardrails.
Empty provider generations are retryable failures so an eligible governed fallback can serve them.

Key classes:
- LlmClient: Catalog-driven guardrailed async client.

Key functions:
- (none)

Notes:
- Private adapters are never exported from fraudlens_llm.__all__.
- Undeclared tool calls fail closed unless a caller explicitly captures them for audit/refusal.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any, cast

from fraudlens_llm.adapters.anthropic import AnthropicAdapter
from fraudlens_llm.adapters.base import (
    AdapterGenerateResult,
    ProviderAdapter,
    StreamingProviderAdapter,
)
from fraudlens_llm.adapters.openai_compatible import OpenAiCompatibleAdapter
from fraudlens_llm.catalog import Catalog, GenerationParams, Kind, load_catalog
from fraudlens_llm.client_binding import (
    BoundModel,
    StreamGenerationRequest,
    collect_adapter_stream,
)
from fraudlens_llm.client_guardrails import (
    NOT_APPLICABLE,
    _generation_guardrail_report,
    _mask_messages,
    _safe_log_success,
    _validate_tool_calls_for_capture,
    coerce_messages,
    embedding_guardrail_report,
    generation_result,
    mask_inputs,
    merge_params,
)
from fraudlens_llm.client_guardrails import (
    estimate_cost as _estimate_cost,
)
from fraudlens_llm.client_resolution import (
    InputGuardrails,
    PreparedGeneration,
    ResolvedModel,
    eligible_fallbacks,
    enforce_provider_policy,
    require_generation_capabilities,
    require_kind,
    resolve_model,
)
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
    TaskType,
    ToolDefinition,
)
from fraudlens_llm.providers import (
    Protocol,
    Providers,
    load_providers,
)
from fraudlens_llm.security.phi import mask_texts
from fraudlens_llm.security.policy import policy_outcome, system_policy_message
from fraudlens_llm.security.prompt_risk import scan_prompt_risk
from fraudlens_llm.security.tools import (
    validate_response_schema,
    validate_tool_definitions,
)
from fraudlens_llm.settings import LlmSettings, get_llm_settings

__all__ = ["BoundModel", "LlmClient", "StreamGenerationRequest", "_estimate_cost", "mask_texts"]


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
        resolved = resolve_model(self._catalog, self._providers, model)
        require_kind(resolved, Kind.EMBED)
        if resolved.provider_config.protocol != Protocol.OPENAI_COMPATIBLE:
            raise CapabilityMismatchError("Embeddings require an openai_compatible provider")
        resolved_data_class = data_class or self._settings.default_data_class
        enforce_provider_policy(resolved, resolved_data_class)
        masked_inputs, masking_report = mask_inputs(inputs, self._settings)
        guardrail = embedding_guardrail_report(
            settings=self._settings,
            masking_report=masking_report,
            policy=policy_outcome(allowed=True),
        )
        params = merge_params(None, resolved.card.default_params, overrides)
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
        resolve_model(self._catalog, self._providers, ref)
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
    ) -> PreparedGeneration:
        """Resolve policy and input guardrails once for blocking or streaming transport."""
        resolved = resolve_model(
            self._catalog,
            self._providers,
            model or self._settings.default_model,
        )
        require_kind(resolved, Kind.CHAT)
        resolved_data_class = data_class or self._settings.default_data_class
        enforce_provider_policy(resolved, resolved_data_class)
        resolved_tools = tuple(tools or ())
        validate_tool_definitions(resolved_tools, tool_choice=tool_choice)
        validate_response_schema(response_schema)
        require_generation_capabilities(
            resolved,
            tools=resolved_tools,
            response_schema=response_schema,
        )
        input_guardrails = self._run_input_guardrails(
            coerce_messages(messages),
            task_type=task_type,
            data_class=resolved_data_class,
            tools=resolved_tools,
            capture_undeclared_tool_calls=capture_undeclared_tool_calls,
        )
        return PreparedGeneration(
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
        resolved: ResolvedModel,
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
            *eligible_fallbacks(
                self._catalog,
                self._providers,
                resolved,
                data_class,
                fallbacks,
                tools=tools,
                response_schema=response_schema,
                allow_policy_downgrade=self._settings.allow_policy_downgrade,
            ),
        ]
        for fallback_count, target in enumerate(eligible):
            params = merge_params(
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
            return generation_result(
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
        target: ResolvedModel,
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
            return await collect_adapter_stream(
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

    def _run_input_guardrails(
        self,
        messages: Sequence[LlmMessage],
        *,
        task_type: TaskType,
        data_class: DataClass,
        tools: Sequence[ToolDefinition],
        capture_undeclared_tool_calls: bool,
    ) -> InputGuardrails:
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
            output=NOT_APPLICABLE,
            phishing=NOT_APPLICABLE,
            policy=policy_outcome(allowed=True),
        )
        return InputGuardrails(messages=masked_messages, report=report)

    def _adapter_for(self, resolved: ResolvedModel) -> ProviderAdapter:
        """Return or create the private adapter for a provider."""
        adapter = self._adapters.get(resolved.provider)
        if adapter is not None:
            return adapter
        if resolved.provider_config.protocol == Protocol.OPENAI_COMPATIBLE:
            adapter = OpenAiCompatibleAdapter(
                resolved.provider,
                resolved.provider_config,
                self._settings,
            )
        elif resolved.provider_config.protocol == Protocol.ANTHROPIC:
            adapter = AnthropicAdapter(resolved.provider, resolved.provider_config)
        else:
            raise CapabilityMismatchError(f"Unsupported provider protocol for {resolved.provider}")
        self._adapters[resolved.provider] = adapter
        return adapter

    def _resolve_model(self, ref: str) -> ResolvedModel:
        """Retain the established private resolution seam used by focused tests."""
        return resolve_model(self._catalog, self._providers, ref)

    def _streaming_adapter_for(self, resolved: ResolvedModel) -> StreamingProviderAdapter:
        """Return the native streaming adapter supported by OpenAI-compatible providers."""
        if resolved.provider_config.protocol != Protocol.OPENAI_COMPATIBLE:
            raise CapabilityMismatchError(
                "Native chat streaming requires an openai_compatible provider"
            )
        return cast(StreamingProviderAdapter, self._adapter_for(resolved))
