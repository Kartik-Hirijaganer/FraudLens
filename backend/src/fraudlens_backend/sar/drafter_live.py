"""Summary: The live, provider-backed SAR drafter (plan §7, §16 Phase 7). `LiveSarDrafter`
implements the injected `fraudlens_ml.sar.SarDrafter` protocol on top of the guardrailed
`fraudlens_llm` client — the ONLY call path that can reach a provider, and the reason ml never
imports llm (the backend wires it in). Per draft it: (1) replays an identical prior result from the
cache with no spend (plan §7.6); (2) enforces the session/daily USD budget, raising
`SarBudgetExceededError` → 429 before any call (plan §7.6); (3) assembles the PHI-masked prompt and
calls the client's provider-native streaming path — optionally under CONSTRAINED DECODING, whose
compact citation schema makes fabricated ids structurally impossible — and assembles raw deltas
server-side before running output policy/phishing scans + sanitization + governed fallback (plan
§8.1, §7.5); (4) parses the compact envelope and deterministically hydrates evidence refs, asserted
values, section headings, and the human-review action from trusted backend data; (5) evaluates that
ungrounded candidate with the deterministic `SarQualityGate`; (6) grounds, renders, records
usage/cost, and caches ONLY a gate-passing draft; (7) streams the validated result. A rejected
candidate becomes a terminal `failed` result carrying its verdict, so the cascade above can
escalate on evidence rather than on a guess.

Key classes:
- LiveSarDrafter: the provider-backed SarDrafter (gate, guardrails, grounding, cost, cache).

Key functions:
- (none)

Notes:
- The model reference, connection route, fallback chain, and output-token cap are injected
  (config-driven, never hardcoded — plan §7.2); `task_type=ANALYSIS` so injection-shaped regulatory
  text FLAGS for human review rather than hard-blocking the draft (plan §8.5).
- `fallbacks` remains the TRANSPORT fallback owned by the client (retryable provider errors).
  Quality escalation is a different concern at a different layer and lives in the cascade above
  (AD-2.3); the self-hosted profiles pass no fallbacks so a tier failure never leaves the GPU.
- Every draft, passing or rejected, carries one `SarGenerationAttempt` so spend, route, and the
  gate verdict are reconstructable from the result alone.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from decimal import Decimal

from fraudlens_backend.sar.budget import BudgetGuard, estimate_cost_usd
from fraudlens_backend.sar.cache import SarCacheGenerationSettings, SarDraftCache, sar_cache_key
from fraudlens_backend.sar.egress import (
    EgressBlockedError,
    EgressPolicy,
    SarModelInput,
    load_egress_policy,
    project_for_model,
)
from fraudlens_backend.sar.evidence import SarEvidenceCatalog, build_evidence_catalog
from fraudlens_backend.sar.prompt import SarPromptTemplate, build_messages
from fraudlens_backend.sar.quality_gate import SarQualityGate
from fraudlens_backend.sar.schema import (
    SarSchemaError,
    ground_citations,
    hydrate_generation,
    parse_generation,
    render_markdown,
    sar_response_schema,
)
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
    SarDraftContent,
    SarDraftResult,
    SarDraftStatus,
    SarGateReason,
    SarGenerationAttempt,
    SarInput,
    SarQualityGateResult,
    SarStreamEvent,
    SarTokenUsage,
)

GATE_FAILED_CODE = "sar_quality_gate_failed"
_MS_PER_SECOND = 1000
_OUTCOME_PASSED = "passed"
_OUTCOME_REJECTED = "rejected"
_OUTCOME_ERROR = "error"


class LiveSarDrafter:
    """The provider-backed SarDrafter: quality gate, guardrails, grounding, cost, cache."""

    def __init__(  # noqa: PLR0913 - explicit injected collaborators (DI; no hidden globals).
        self,
        *,
        client: LlmClient,
        catalog: Catalog,
        prompt: SarPromptTemplate,
        model: str,
        max_output_tokens: int,
        gate: SarQualityGate,
        reasoning_effort: str | None = None,
        budget: BudgetGuard,
        cache: SarDraftCache,
        fallbacks: tuple[str, ...] = (),
        task_type: TaskType = TaskType.ANALYSIS,
        egress_policy: EgressPolicy | None = None,
        stage: str = "primary",
        connection: str | None = None,
        constrained_decoding: bool = False,
    ) -> None:
        """Bind the guardrailed client, catalog, prompt, route, gate, budget, and cache."""
        self._client = client
        self._catalog = catalog
        self._prompt = prompt
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._gate = gate
        self._reasoning_effort = reasoning_effort
        self._budget = budget
        self._cache = cache
        self._fallbacks = fallbacks
        self._task_type = task_type
        self._egress_policy = egress_policy or load_egress_policy()
        self._stage = stage
        self._connection = connection
        self._constrained_decoding = constrained_decoding

    async def draft(self, sar_input: SarInput) -> AsyncIterator[SarStreamEvent]:
        """Replay-or-native-stream a guarded SAR, gate it, then emit only an accepted rendering."""
        try:
            model_input = project_for_model(sar_input, self._egress_policy)
        except EgressBlockedError as exc:
            async for event in stream_result(self._failed_result(exc.code)):
                yield event
            return
        key = self._cache_key(sar_input, model_input)
        cached = self._cache.get(key)
        if cached is not None:
            async for event in stream_result(cached.model_copy(update={"cached": True})):
                yield event
            return

        self._budget.ensure_within_budget()
        evidence_catalog = build_evidence_catalog(model_input)
        started = time.perf_counter()
        try:
            llm_result = await self._client.generate_stream(
                self._request(
                    build_messages(self._prompt, model_input), sar_input, evidence_catalog
                )
            )
        except LlmError as exc:
            async for event in stream_result(
                self._failed_result(_error_code(exc), latency_ms=_elapsed_ms(started))
            ):
                yield event
            return

        latency_ms = _elapsed_ms(started)
        try:
            content = hydrate_generation(parse_generation(llm_result.safe_text), evidence_catalog)
        except SarSchemaError:
            verdict = self._gate.rejected(SarGateReason.SCHEMA_INVALID)
            async for event in stream_result(
                self._failed_result(
                    GATE_FAILED_CODE,
                    llm_result=llm_result,
                    quality=verdict,
                    latency_ms=latency_ms,
                )
            ):
                yield event
            return

        verdict = self._gate.evaluate(
            content,
            available=sar_input.citations,
            catalog=evidence_catalog,
            finish_reason=llm_result.finish_reason,
        )
        if not verdict.passed:
            async for event in stream_result(
                self._failed_result(
                    GATE_FAILED_CODE,
                    llm_result=llm_result,
                    quality=verdict,
                    latency_ms=latency_ms,
                )
            ):
                yield event
            return

        result = self._accepted_result(content, sar_input, llm_result, verdict, latency_ms)
        self._cache.set(key, result)
        async for event in stream_result(result):
            yield event

    def _request(
        self,
        messages: list[dict[str, object]],
        sar_input: SarInput,
        evidence_catalog: SarEvidenceCatalog,
    ) -> StreamGenerationRequest:
        """Build the guarded request, closing citations and evidence when constrained."""
        return StreamGenerationRequest(
            messages=[LlmMessage.model_validate(message) for message in messages],
            model=self._model,
            overrides=GenerationParams(
                max_tokens=self._max_output_tokens,
                response_format="json_object",
                reasoning_effort=self._reasoning_effort,
            ),
            task_type=self._task_type,
            fallbacks=self._fallbacks,
            connection=self._connection,
            response_schema=(
                sar_response_schema(sar_input.citations, evidence_catalog)
                if self._constrained_decoding
                else None
            ),
        )

    def _cache_key(self, sar_input: SarInput, model_input: SarModelInput) -> str:
        """Fingerprint tenant, evidence, prompt, route, policy, and generation settings."""
        return sar_cache_key(
            model_id=self._model,
            prompt_hash=self._prompt.prompt_hash,
            agency_id=sar_input.agency_id,
            model_input=model_input,
            generation_settings=SarCacheGenerationSettings(
                max_output_tokens=self._max_output_tokens,
                reasoning_effort=self._reasoning_effort,
                fallbacks=self._fallbacks,
                task_type=self._task_type.value,
                profile_stage=self._stage,
                connection=self._connection,
                constrained_decoding=self._constrained_decoding,
                prompt_version=self._prompt.prompt_version,
                quality_policy_hash=self._gate.policy_hash,
            ),
        )

    def _accepted_result(
        self,
        content: SarDraftContent,
        sar_input: SarInput,
        llm_result: LlmResult,
        verdict: SarQualityGateResult,
        latency_ms: int,
    ) -> SarDraftResult:
        """Ground, render, and price one gate-passing candidate into a terminal draft."""
        grounded_ids, grounded = ground_citations(content.cited_regulations, sar_input.citations)
        body = content.model_copy(update={"cited_regulations": grounded_ids})
        cost = self._record_spend(llm_result)
        return self._served(llm_result, cost, SarDraftStatus.DRAFT).model_copy(
            update={
                "content": render_markdown(body),
                "structured": body,
                "citations": grounded,
                "quality": verdict,
                "attempts": (
                    self._attempt(_OUTCOME_PASSED, llm_result, verdict, latency_ms, cost=cost),
                ),
            }
        )

    def _failed_result(
        self,
        error_code: str,
        *,
        llm_result: LlmResult | None = None,
        quality: SarQualityGateResult | None = None,
        latency_ms: int = 0,
    ) -> SarDraftResult:
        """Build a terminal failure, retaining provider accounting when a call completed."""
        if llm_result is None:
            return SarDraftResult(
                status=SarDraftStatus.FAILED,
                model_id=self._model,
                prompt_version=self._prompt.prompt_version,
                prompt_hash=self._prompt.prompt_hash,
                error_code=error_code,
                quality=quality,
                attempts=(
                    self._attempt(_OUTCOME_ERROR, None, quality, latency_ms, error_code=error_code),
                ),
            )
        cost = self._record_spend(llm_result)
        return self._served(llm_result, cost, SarDraftStatus.FAILED).model_copy(
            update={
                "error_code": error_code,
                "quality": quality,
                "attempts": (
                    self._attempt(
                        _OUTCOME_REJECTED,
                        llm_result,
                        quality,
                        latency_ms,
                        cost=cost,
                        error_code=error_code,
                    ),
                ),
            }
        )

    def _record_spend(self, llm_result: LlmResult) -> Decimal:
        """Price one completed provider call and charge it to the session budget."""
        cost = self._estimate_cost(llm_result.model, llm_result.usage)
        self._budget.record(cost)
        return cost

    def _served(
        self, llm_result: LlmResult, cost: Decimal, status: SarDraftStatus
    ) -> SarDraftResult:
        """Build the route, prompt, usage, and spend provenance every completed call records."""
        return SarDraftResult(
            status=status,
            model_id=llm_result.model,
            provider=llm_result.provider,
            prompt_version=self._prompt.prompt_version,
            prompt_hash=self._prompt.prompt_hash,
            token_usage=_usage(llm_result.usage),
            cost_usd=cost,
            fallback_count=0 if llm_result.model == self._model else 1,
            guardrail_decision=llm_result.guardrail.decision.value,
        )

    def _attempt(  # noqa: PLR0913 - one argument per recorded audit field.
        self,
        outcome: str,
        llm_result: LlmResult | None,
        quality: SarQualityGateResult | None,
        latency_ms: int,
        *,
        cost: Decimal = Decimal("0"),
        error_code: str | None = None,
    ) -> SarGenerationAttempt:
        """Record one PHI-free audit row for this stage's single generation."""
        return SarGenerationAttempt(
            ordinal=0,
            stage=self._stage,
            model_id=self._model,
            connection=self._connection,
            served_model=llm_result.served_model if llm_result is not None else None,
            outcome=outcome,
            error_code=error_code,
            quality=quality,
            latency_ms=latency_ms,
            token_usage=_usage(llm_result.usage) if llm_result is not None else SarTokenUsage(),
            cost_usd=cost,
            prompt_hash=self._prompt.prompt_hash,
            policy_hash=self._gate.policy_hash,
        )

    def _estimate_cost(self, model_ref: str, usage: LlmUsage) -> Decimal:
        """Price the served model's usage from the catalog card (Decimal('0') if unknown)."""
        try:
            _, _, card = self._catalog.get(model_ref)
        except ModelNotFoundError:
            return Decimal("0")
        return estimate_cost_usd(card, usage)


def _elapsed_ms(started: float) -> int:
    """Return whole milliseconds elapsed since a perf-counter mark."""
    return int((time.perf_counter() - started) * _MS_PER_SECOND)


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
