"""Summary: The live, provider-backed SAR drafter (plan §7, §16 Phase 7). `LiveSarDrafter`
implements the injected `fraudlens_ml.sar.SarDrafter` protocol on top of the guardrailed
`fraudlens_llm` client — the ONLY call path that can reach a provider, and the reason ml never
imports llm (the backend wires it in). Per draft it: (1) replays an identical prior result from the
cache with no spend (plan §7.6); (2) enforces the session/daily USD budget, raising
`SarBudgetExceededError` → 429 before any call (plan §7.6); (3) assembles the PHI-masked prompt and
calls the client's provider-native streaming path, which assembles raw deltas server-side before
running output policy/phishing scans + sanitization + governed fallback (plan §8.1, §7.5) — so no
unvalidated partial JSON reaches an analyst; (4) parses the complete model JSON into a schema-valid,
citation-GROUNDED `SarDraftContent` (no
fabricated regulation ids); (5) records token usage + estimated cost for the audit trail (plan §7.4)
 and caches the result; (6) streams only the validated rendered result. A schema-invalid provider
response retains its token and cost telemetry before degrading. Any provider/guardrail/schema
failure degrades to a terminal
`failed` result so the run still completes with score+SHAP+RAG (plan §7.5) — it never throws except
for the budget 429.

Key classes:
- LiveSarDrafter: the provider-backed SarDrafter (guardrails, grounding, cost, cache, fallback).

Key functions:
- (none)

Notes:
- The model reference + fallback chain + max output tokens are injected (config-driven, never
  hardcoded — plan §7.2); `task_type=ANALYSIS` so injection-shaped regulatory text FLAGS for human
  review rather than hard-blocking the draft (plan §8.5), while output policy/phishing still block.
- It depends on the concrete guardrailed `LlmClient` (the backend may import llm; ml never can):
  the pipeline-facing seam is the `SarDrafter` protocol this class satisfies, not the client.
- `fallback_count` is best-effort (1 when the served ref differs from the requested one): the exact
  hop count lives in the client's safe LLM logs (plan §7.4), not in this PHI-free result.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

from fraudlens_backend.sar.budget import BudgetGuard, estimate_cost_usd
from fraudlens_backend.sar.cache import SarDraftCache, sar_cache_key
from fraudlens_backend.sar.prompt import SarPromptTemplate, build_messages
from fraudlens_backend.sar.schema import SarSchemaError, parse_and_ground, render_markdown
from fraudlens_backend.sar.streaming import stream_result
from fraudlens_llm import (
    Catalog,
    GenerationParams,
    GuardrailError,
    LlmClient,
    LlmError,
    LlmMessage,
    LlmRateLimitError,
    LlmResult,
    LlmTimeoutError,
    LlmUsage,
    ModelNotFoundError,
    PolicyError,
    StreamGenerationRequest,
    TaskType,
)
from fraudlens_ml.sar import (
    SarDraftResult,
    SarDraftStatus,
    SarInput,
    SarStreamEvent,
    SarTokenUsage,
)


class LiveSarDrafter:
    """The provider-backed SarDrafter: guardrails, grounding, cost, cache, governed fallback."""

    def __init__(  # noqa: PLR0913 - explicit injected collaborators (DI; no hidden globals).
        self,
        *,
        client: LlmClient,
        catalog: Catalog,
        prompt: SarPromptTemplate,
        model: str,
        max_output_tokens: int,
        reasoning_effort: str | None = None,
        budget: BudgetGuard,
        cache: SarDraftCache,
        fallbacks: tuple[str, ...] = (),
        task_type: TaskType = TaskType.ANALYSIS,
    ) -> None:
        """Bind the guardrailed client, pricing catalog, prompt, model, budget, and cache."""
        self._client = client
        self._catalog = catalog
        self._prompt = prompt
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._reasoning_effort = reasoning_effort
        self._budget = budget
        self._cache = cache
        self._fallbacks = fallbacks
        self._task_type = task_type

    async def draft(self, sar_input: SarInput) -> AsyncIterator[SarStreamEvent]:
        """Replay-or-native-stream a guarded SAR, then emit only its validated rendering."""
        key = sar_cache_key(self._model, self._prompt.prompt_hash, sar_input)
        cached = self._cache.get(key)
        if cached is not None:
            async for event in stream_result(cached.model_copy(update={"cached": True})):
                yield event
            return

        self._budget.ensure_within_budget()
        messages = build_messages(self._prompt, sar_input)
        try:
            llm_result = await self._client.generate_stream(
                StreamGenerationRequest(
                    messages=[LlmMessage.model_validate(message) for message in messages],
                    model=self._model,
                    overrides=GenerationParams(
                        max_tokens=self._max_output_tokens,
                        response_format="json_object",
                        reasoning_effort=self._reasoning_effort,
                    ),
                    task_type=self._task_type,
                    fallbacks=self._fallbacks,
                )
            )
        except LlmError as exc:
            async for event in stream_result(self._failed_result(_error_code(exc))):
                yield event
            return

        try:
            content, grounded = parse_and_ground(llm_result.safe_text, sar_input.citations)
        except SarSchemaError:
            async for event in stream_result(
                self._failed_result("sar_schema_invalid", llm_result=llm_result)
            ):
                yield event
            return

        cost = self._estimate_cost(llm_result.model, llm_result.usage)
        self._budget.record(cost)
        result = SarDraftResult(
            status=SarDraftStatus.DRAFT,
            content=render_markdown(content),
            structured=content,
            citations=grounded,
            model_id=llm_result.model,
            provider=llm_result.provider,
            prompt_version=self._prompt.prompt_version,
            prompt_hash=self._prompt.prompt_hash,
            token_usage=_usage(llm_result.usage),
            cost_usd=cost,
            fallback_count=0 if llm_result.model == self._model else 1,
            guardrail_decision=llm_result.guardrail.decision.value,
        )
        self._cache.set(key, result)
        async for event in stream_result(result):
            yield event

    def _failed_result(
        self, error_code: str, *, llm_result: LlmResult | None = None
    ) -> SarDraftResult:
        """Build a terminal failure, retaining provider accounting when a call completed."""
        if llm_result is None:
            return SarDraftResult(
                status=SarDraftStatus.FAILED,
                model_id=self._model,
                prompt_version=self._prompt.prompt_version,
                prompt_hash=self._prompt.prompt_hash,
                error_code=error_code,
            )
        cost = self._estimate_cost(llm_result.model, llm_result.usage)
        self._budget.record(cost)
        return SarDraftResult(
            status=SarDraftStatus.FAILED,
            model_id=llm_result.model,
            provider=llm_result.provider,
            prompt_version=self._prompt.prompt_version,
            prompt_hash=self._prompt.prompt_hash,
            token_usage=_usage(llm_result.usage),
            cost_usd=cost,
            fallback_count=0 if llm_result.model == self._model else 1,
            guardrail_decision=llm_result.guardrail.decision.value,
            error_code=error_code,
        )

    def _estimate_cost(self, model_ref: str, usage: LlmUsage) -> Decimal:
        """Price the served model's usage from the catalog card (Decimal('0') if unknown)."""
        try:
            _, _, card = self._catalog.get(model_ref)
        except ModelNotFoundError:
            return Decimal("0")
        return estimate_cost_usd(card, usage)


def _usage(usage: LlmUsage) -> SarTokenUsage:
    """Project the llm client's usage onto the PHI-free SAR token-usage record."""
    return SarTokenUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
    )


def _error_code(exc: LlmError) -> str:
    """Map an llm exception to a stable, PHI-free failure code for the draft."""
    if isinstance(exc, GuardrailError):
        return "sar_guardrail_blocked"
    if isinstance(exc, PolicyError):
        return "llm_policy_denied"
    if isinstance(exc, LlmRateLimitError):
        return "llm_rate_limited"
    if isinstance(exc, LlmTimeoutError):
        return "llm_timeout"
    return "llm_error"
