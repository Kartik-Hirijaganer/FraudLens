"""Summary: Public guardrailed async LLM client. It is the first exported call path
that can reach provider SDKs and enforces compliance policy, PHI masking, prompt
risk scanning, output scanning/sanitization, safe logging, and fallback governance.

Key classes:
- BoundModel: Convenience wrapper bound to one provider/model reference.
- LlmClient: Catalog-driven guardrailed async client.

Key functions:
- (none)

Notes:
- Private adapters are never exported from fraudlens_llm.__all__.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from fraudlens_llm.adapters.anthropic import AnthropicAdapter
from fraudlens_llm.adapters.base import AdapterGenerateResult, ProviderAdapter
from fraudlens_llm.adapters.openai_compatible import OpenAiCompatibleAdapter
from fraudlens_llm.catalog import Catalog, GenerationParams, Kind, ModelCard, load_catalog
from fraudlens_llm.exceptions import (
    CapabilityMismatchError,
    GuardrailError,
    LlmError,
    PolicyError,
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
        fallbacks: Sequence[str] | None = None,
    ) -> LlmResult:
        """Generate chat text through the guardrailed pipeline."""
        resolved = self._resolve_model(model or self._settings.default_model)
        self._require_kind(resolved, Kind.CHAT)
        resolved_data_class = data_class or self._settings.default_data_class
        self._enforce_provider_policy(resolved, resolved_data_class)
        typed_messages = _coerce_messages(messages)
        input_guardrails = self._run_input_guardrails(
            typed_messages,
            task_type=task_type,
            data_class=resolved_data_class,
        )
        provider_messages = [system_policy_message(), *input_guardrails.messages]
        return await self._generate_with_fallbacks(
            resolved,
            provider_messages,
            input_guardrails.report,
            overrides=overrides,
            task_type=task_type,
            data_class=resolved_data_class,
            include_raw=include_raw,
            fallbacks=fallbacks or (),
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
    ) -> LlmResult:
        """Generate with primary then eligible fallback refs after retryable failures."""
        last_error: LlmError | None = None
        eligible = [resolved, *self._eligible_fallbacks(resolved, data_class, fallbacks)]
        for fallback_count, target in enumerate(eligible):
            params = _merge_params(
                self._settings.default_params,
                target.card.default_params,
                overrides,
            )
            start = time.perf_counter()
            try:
                adapter_result = await self._adapter_for(target).generate(
                    model_id=target.model_id,
                    card=target.card,
                    messages=messages,
                    params=params,
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
            )
        if last_error is not None:
            raise last_error
        raise PolicyError("No fallback model satisfied provider governance policy")

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
    ) -> _InputGuardrails:
        """Run PHI masking and prompt-risk checks before any adapter call."""
        _ = data_class
        masked_texts, masking_report = _mask_inputs(
            [message.content for message in messages],
            self._settings,
        )
        masked_messages = [
            LlmMessage(role=message.role, content=masked_text)
            for message, masked_text in zip(messages, masked_texts, strict=True)
        ]
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

    def _eligible_fallbacks(
        self,
        primary: _ResolvedModel,
        data_class: DataClass,
        fallback_refs: Sequence[str],
    ) -> list[_ResolvedModel]:
        """Return fallbacks that do not weaken provider governance posture."""
        eligible: list[_ResolvedModel] = []
        for ref in fallback_refs:
            candidate = self._resolve_model(ref)
            self._require_kind(candidate, Kind.CHAT)
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
) -> LlmResult:
    """Scan raw output, sanitize it, safe-log success, and build public result."""
    output, phishing = scan_output_risk(
        adapter_result.text,
        strictness=settings.guardrail_strictness,
        task_type=task_type,
    )
    if GuardrailDecision.BLOCK in {output.decision, phishing.decision}:
        raise GuardrailError("Output guardrails blocked the LLM response")
    final_guardrail = _generation_guardrail_report(
        settings=settings,
        masking_report=guardrail.masking,
        prompt_risk=guardrail.prompt_risk,
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
