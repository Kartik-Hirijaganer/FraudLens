"""Summary: Input/output masking, scanning, result assembly, and safe logging for LlmClient.

Key classes:
- (none)

Key functions:
- coerce_messages: validate public message inputs.
- mask_inputs: apply deterministic PHI masking with fail-closed errors.
- merge_params:
- generation_result: assemble a guarded public generation result.
- embedding_guardrail_report: assemble embedding guardrail outcomes.
- estimate_cost: estimate token cost from verified catalog pricing.

Notes:
- No prompt or model output text is emitted to logs.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping, Sequence

from pydantic import TypeAdapter

from fraudlens_llm.adapters.base import (
    AdapterGenerateResult,
)
from fraudlens_llm.catalog import GenerationParams, ModelCard
from fraudlens_llm.client_resolution import ResolvedModel
from fraudlens_llm.exceptions import (
    GuardrailError,
)
from fraudlens_llm.models import (
    DataClass,
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
from fraudlens_llm.security.output import sanitize_output
from fraudlens_llm.security.phishing import scan_output_risk
from fraudlens_llm.security.prompt_risk import scan_prompt_risk
from fraudlens_llm.security.redaction import safe_log_event
from fraudlens_llm.security.tools import (
    validate_tool_calls,
)
from fraudlens_llm.settings import LlmSettings

_LOGGER = logging.getLogger(__name__)
_MESSAGE_ADAPTER: TypeAdapter[list[LlmMessage]] = TypeAdapter(list[LlmMessage])
_TOKENS_PER_MILLION = 1_000_000
NOT_APPLICABLE = ScanOutcome(decision=GuardrailDecision.NOT_APPLICABLE, findings=[])


def coerce_messages(messages: Sequence[LlmMessage | dict[str, object]]) -> list[LlmMessage]:
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

    masked_texts, masking_report = mask_inputs(raw_texts, settings)
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
    masked_texts, report = mask_inputs(serialized, settings)
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


def mask_inputs(
    inputs: Sequence[str],
    settings: LlmSettings,
) -> tuple[list[str], MaskingReport]:
    """Mask inputs and fail closed if local masking raises."""
    try:
        from fraudlens_llm import client as client_module  # noqa: PLC0415

        return client_module.mask_texts(inputs, settings.phi_masking_mode)
    except Exception as exc:
        raise GuardrailError("PHI masking failed closed") from exc


def merge_params(
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


def generation_result(  # noqa: PLR0913 - assembles result from explicit pipeline stages.
    *,
    target: ResolvedModel,
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


def embedding_guardrail_report(
    *,
    settings: LlmSettings,
    masking_report: MaskingReport,
    policy: ScanOutcome,
) -> GuardrailReport:
    """Build an embedding guardrail report with output stages marked not applicable."""
    return _generation_guardrail_report(
        settings=settings,
        masking_report=masking_report,
        prompt_risk=NOT_APPLICABLE,
        output=NOT_APPLICABLE,
        phishing=NOT_APPLICABLE,
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
    resolved: ResolvedModel,
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
                estimated_cost_usd=estimate_cost(resolved.card, usage),
                fallback_count=fallback_count,
            )
        },
    )


def estimate_cost(card: ModelCard, usage: LlmUsage) -> float | None:
    """Estimate token cost when verified token pricing is available."""
    if card.pricing_basis != "per_million_tokens":
        return None
    if card.input_price_per_million is None and card.output_price_per_million is None:
        return None
    input_cost = usage.input_tokens * (card.input_price_per_million or 0) / _TOKENS_PER_MILLION
    output_cost = usage.output_tokens * (card.output_price_per_million or 0) / _TOKENS_PER_MILLION
    return input_cost + output_cost
