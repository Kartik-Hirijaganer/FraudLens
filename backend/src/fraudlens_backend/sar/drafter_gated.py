"""Summary: The quality-gated SAR cascade (release 0.5.0 Phase 2.5) — the drafter the resume
bullet is named after. `QualityGatedSarDrafter` is a `SarDrafter` that wraps an ordered tuple of
`SarDrafter` tiers (AWQ → BF16 → external) and serves the FIRST candidate whose deterministic
`SarQualityGate` verdict passes; when every tier is rejected the cascade fails EXPLICITLY instead
of quietly returning a shortened citation list. It is modelled on `LiveAgentFallbackDrafter`: a
decorator behind the same protocol, so nothing downstream learns a new type.
Three properties matter more than the routing. First, it BUFFERS: a tier's token events are
consumed and discarded, and `sar.token` is emitted only for the accepted result, so text a
rejected AWQ draft produced can never reach a client. Second, it is FINITE: tiers are a fixed
ordered tuple consumed by index with exactly one generation each — a deterministic gate failure is
not transient, so retrying the same tier at temperature 0 is pure waste — and no tier may reference
another. Third, it PREFLIGHTS: a case with no egress-eligible evidence — or, under a policy
that requires one, nothing to cite — is rejected before any model call, so a case that cannot
succeed at any tier costs nothing.

Key classes:
- SarCascadeTier: one named stage binding a drafter and its egress requirement.
- SarCascadeConfigError: raised when a cascade's stage list violates the runtime policy.
- QualityGatedSarDrafter: the ordered, gate-driven, explicitly failing SAR cascade.

Key functions:
- (none)

Notes:
- Escalation happens on an exhausted retryable provider error, timeout, rate limit, empty output,
  malformed schema, or gate failure. It NEVER happens on an egress refusal, a policy denial, or
  insufficient source evidence — those fail the same way at every tier, so spending again is waste.
- A budget denial propagates as `SarBudgetExceededError` (HTTP 429) exactly as for one model; the
  budget is checked before EACH tier, not once per request.
- Lifecycle events carry stage names and reason codes only — never model text, never PHI.
  `sar.escalated` names the stage being escalated TO and is emitted once per advance, so the exact
  number of escalations a case paid for is countable from the stream alone.
- Only the ACCEPTED result is streamed, through the same `stream_result` helper every drafter
  uses, so an analyst sees exactly the artifact that was persisted and nothing a tier produced
  before it was rejected.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from fraudlens_backend.sar.budget import BudgetGuard
from fraudlens_backend.sar.drafter_live import GATE_FAILED_CODE
from fraudlens_backend.sar.egress import (
    EgressBlockedError,
    EgressPolicy,
    load_egress_policy,
    project_for_model,
)
from fraudlens_backend.sar.evidence import build_evidence_catalog
from fraudlens_backend.sar.quality_gate import SarQualityGate
from fraudlens_backend.sar.streaming import stream_result
from fraudlens_ml.sar import (
    SarDrafter,
    SarDraftResult,
    SarDraftStatus,
    SarEventType,
    SarGateReason,
    SarGenerationAttempt,
    SarInput,
    SarQualityGateResult,
    SarStageEvent,
    SarStreamEvent,
    SarTokenUsage,
)

EGRESS_TIER_NOT_ALLOWED = "egress_tier_not_allowed"
_CASCADE_NO_RESULT = "sar_cascade_no_result"
_NON_ESCALATABLE_CODES = frozenset({"llm_policy_denied", "sar_guardrail_blocked"})
_EGRESS_CODE_PREFIX = "egress_"


class SarCascadeConfigError(RuntimeError):
    """Raised when a cascade declares no stage, duplicate stages, or too many stages."""


class SarCascadeTier(BaseModel):
    """One named cascade stage: the drafter to run and the egress class it requires."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    name: str = Field(..., min_length=1, description="Configured stage name (e.g. 'awq').")
    drafter: SarDrafter = Field(..., description="The drafter this stage runs exactly once.")
    requires_egress_class: str | None = Field(
        default=None, description="Data class the projected case must carry to use this stage."
    )


class QualityGatedSarDrafter:
    """Serve the first gate-passing tier, escalate on rejection, and fail explicitly at the end."""

    def __init__(
        self,
        *,
        tiers: tuple[SarCascadeTier, ...],
        gate: SarQualityGate,
        budget: BudgetGuard,
        egress_policy: EgressPolicy | None = None,
    ) -> None:
        """Bind a finite, unique, policy-bounded stage list with its gate and budget guard."""
        names = [tier.name for tier in tiers]
        if not tiers:
            raise SarCascadeConfigError("A SAR cascade requires at least one stage")
        if len(set(names)) != len(names):
            raise SarCascadeConfigError("SAR cascade stage names must be unique")
        if len(tiers) > gate.policy.max_tiers:
            raise SarCascadeConfigError(
                f"SAR cascade declares {len(tiers)} stages above the policy maximum"
            )
        self._tiers = tiers
        self._gate = gate
        self._budget = budget
        self._egress_policy = egress_policy or load_egress_policy()

    async def draft(self, sar_input: SarInput) -> AsyncIterator[SarStreamEvent]:
        """Run stages in order until one is accepted, then stream only that result."""
        preflight = self._preflight(sar_input)
        if preflight is not None:
            async for event in stream_result(preflight):
                yield event
            return

        attempts: list[SarGenerationAttempt] = []
        rejected: list[str] = []
        last: SarDraftResult | None = None
        for ordinal, tier in enumerate(self._tiers):
            self._budget.ensure_within_budget()
            if rejected:
                yield _stage_event(SarEventType.ESCALATED, tier, ordinal)
            if not self._tier_is_eligible(tier, sar_input):
                yield _stage_event(
                    SarEventType.STAGE_REJECTED, tier, ordinal, error_code=EGRESS_TIER_NOT_ALLOWED
                )
                rejected.append(tier.name)
                continue
            yield _stage_event(SarEventType.STAGE_STARTED, tier, ordinal)
            last = await _consume(tier, sar_input)
            attempts.extend(_renumber(last.attempts, len(attempts)))
            if last.status is SarDraftStatus.DRAFT:
                accepted = _accepted(last, tuple(attempts), ordinal, tuple(rejected))
                async for event in stream_result(accepted):
                    yield event
                return
            yield _stage_event(
                SarEventType.STAGE_REJECTED,
                tier,
                ordinal,
                reasons=last.quality.reasons if last.quality is not None else (),
                error_code=last.error_code,
            )
            rejected.append(tier.name)
            if not _is_escalatable(last):
                break
        yield SarStreamEvent(
            type=SarEventType.CASCADE_FAILED,
            stage=SarStageEvent(stage=self._tiers[-1].name, ordinal=len(self._tiers) - 1),
        )
        async for event in stream_result(
            _exhausted(last, tuple(attempts), tuple(rejected), self._tiers[0].name)
        ):
            yield event

    def _preflight(self, sar_input: SarInput) -> SarDraftResult | None:
        """Reject an ineligible or ungradeable case before any model call, or return None."""
        try:
            model_input = project_for_model(sar_input, self._egress_policy)
        except EgressBlockedError as exc:
            return _terminal_failure(self._tiers[0].name, exc.code, None)
        if not build_evidence_catalog(model_input).facts:
            return self._refused(SarGateReason.EVIDENCE_EMPTY, sar_input)
        # Retrieval is a soft enhancer that degrades to empty, so a case can arrive with nothing
        # to cite. Under a policy that requires a citation no tier can serve it — the constrained
        # schema forbids citing anything and an unconstrained tier could only invent one — so the
        # cascade would otherwise pay for every stage, including the hosted one, to learn that.
        if self._gate.policy.require_citation and not sar_input.citations:
            return self._refused(SarGateReason.NO_CITATIONS, sar_input)
        return None

    def _refused(self, reason: SarGateReason, sar_input: SarInput) -> SarDraftResult:
        """Build the zero-spend terminal failure for a case no tier could have served."""
        return _terminal_failure(
            self._tiers[0].name,
            GATE_FAILED_CODE,
            self._gate.rejected(reason, available=sar_input.citations),
        )

    def _tier_is_eligible(self, tier: SarCascadeTier, sar_input: SarInput) -> bool:
        """Return whether the projected case carries the data class this stage demands."""
        if tier.requires_egress_class is None:
            return True
        data_class = self._egress_policy.source_data_classes.get(sar_input.source)
        return data_class == tier.requires_egress_class


async def _consume(tier: SarCascadeTier, sar_input: SarInput) -> SarDraftResult:
    """Drain one stage, discarding its tokens, and return its terminal result."""
    terminal: SarDraftResult | None = None
    async for event in tier.drafter.draft(sar_input):
        if event.result is not None:
            terminal = event.result
    if terminal is None:
        return _terminal_failure(tier.name, _CASCADE_NO_RESULT, None)
    return terminal


def _renumber(
    attempts: tuple[SarGenerationAttempt, ...], offset: int
) -> tuple[SarGenerationAttempt, ...]:
    """Renumber a stage's attempts onto the cascade's running ordinal sequence."""
    return tuple(
        attempt.model_copy(update={"ordinal": offset + index})
        for index, attempt in enumerate(attempts)
    )


def _is_escalatable(result: SarDraftResult) -> bool:
    """Return whether a rejected stage justifies spending on the next one."""
    code = result.error_code or ""
    if code in _NON_ESCALATABLE_CODES or code.startswith(_EGRESS_CODE_PREFIX):
        return False
    if result.quality is not None:
        return result.quality.fallback_required
    return True


def _stage_event(
    event_type: SarEventType,
    tier: SarCascadeTier,
    ordinal: int,
    *,
    reasons: tuple[SarGateReason, ...] = (),
    error_code: str | None = None,
) -> SarStreamEvent:
    """Build one PHI-free lifecycle event carrying stage identity and reason codes only."""
    return SarStreamEvent(
        type=event_type,
        stage=SarStageEvent(
            stage=tier.name, ordinal=ordinal, reasons=reasons, error_code=error_code
        ),
    )


def _accepted(
    result: SarDraftResult,
    attempts: tuple[SarGenerationAttempt, ...],
    ordinal: int,
    rejected: tuple[str, ...],
) -> SarDraftResult:
    """Attach exact cascade accounting to the result the serving tier produced."""
    return _with_accounting(result, attempts, ordinal, rejected)


def _exhausted(
    last: SarDraftResult | None,
    attempts: tuple[SarGenerationAttempt, ...],
    rejected: tuple[str, ...],
    first_stage: str,
) -> SarDraftResult:
    """Build the explicit terminal failure of an exhausted or halted cascade."""
    base = last if last is not None else _terminal_failure(first_stage, GATE_FAILED_CODE, None)
    return _with_accounting(base, attempts, max(len(rejected) - 1, 0), rejected)


def _with_accounting(
    result: SarDraftResult,
    attempts: tuple[SarGenerationAttempt, ...],
    ordinal: int,
    rejected: tuple[str, ...],
) -> SarDraftResult:
    """Replace per-tier accounting with the cascade's exact totals and provenance."""
    return result.model_copy(
        update={
            "attempts": attempts,
            "escalation_tier": ordinal,
            "escalated_from": rejected,
            "token_usage": _total_usage(attempts),
            "cost_usd": _total_cost(attempts),
            "fallback_count": max(len(attempts) - 1, 0),
        }
    )


def _terminal_failure(
    stage: str, error_code: str, quality: SarQualityGateResult | None
) -> SarDraftResult:
    """Build a zero-spend terminal failure for a case no stage may attempt."""
    return SarDraftResult(
        status=SarDraftStatus.FAILED,
        model_id=stage,
        prompt_version=stage,
        prompt_hash=stage,
        error_code=error_code,
        quality=quality,
    )


def _total_usage(attempts: tuple[SarGenerationAttempt, ...]) -> SarTokenUsage:
    """Sum token usage across every attempt the cascade actually made."""
    return SarTokenUsage(
        input_tokens=sum(attempt.token_usage.input_tokens for attempt in attempts),
        output_tokens=sum(attempt.token_usage.output_tokens for attempt in attempts),
        total_tokens=sum(attempt.token_usage.total_tokens for attempt in attempts),
    )


def _total_cost(attempts: tuple[SarGenerationAttempt, ...]) -> Decimal:
    """Sum estimated USD spend across every attempt the cascade actually made."""
    return sum((attempt.cost_usd for attempt in attempts), start=Decimal("0"))
