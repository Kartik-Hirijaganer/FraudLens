"""Unit tests for the quality-gated AWQ→BF16→external SAR cascade (release 0.5.0 Phase 2.5).

The cascade is the feature the resume bullet is named after, so these pin the properties that
make the claim true rather than merely plausible: escalation happens on a gate REJECTION, a
rejected tier's tokens never reach a client, every tier is invoked at most once, non-escalatable
refusals stop the cascade instead of re-spending, and an exhausted cascade fails explicitly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from quality_gates import production_gate

from fraudlens_backend.sar import drafter_gated
from fraudlens_backend.sar.budget import BudgetGuard, SarBudgetExceededError
from fraudlens_backend.sar.drafter_gated import (
    EGRESS_TIER_NOT_ALLOWED,
    QualityGatedSarDrafter,
    SarCascadeConfigError,
    SarCascadeTier,
)
from fraudlens_backend.sar.drafter_live import GATE_FAILED_CODE
from fraudlens_backend.sar.evidence import SarEvidenceCatalog
from fraudlens_ml.sar import (
    SarDraftResult,
    SarDraftStatus,
    SarEventType,
    SarGateReason,
    SarGenerationAttempt,
    SarInput,
    SarStreamEvent,
    SarTokenUsage,
)

_REJECTED = production_gate().rejected(SarGateReason.CITATION_FABRICATED)
_TERMINAL = production_gate().rejected(SarGateReason.EVIDENCE_EMPTY)
_PASSED = _REJECTED.model_copy(update={"passed": True, "reasons": (), "fallback_required": False})
_SCHEMA_INVALID = production_gate().rejected(SarGateReason.SCHEMA_INVALID)
_OUTCOME_FOR_FAILURE = "rejected"


class _ScriptedTier:
    """A drafter that streams tokens then one scripted terminal result, recording its calls."""

    def __init__(
        self,
        stage: str,
        *,
        passes: bool = True,
        error_code: str | None = None,
        quality: object | None = None,
        cost: str = "0.001",
    ) -> None:
        self.stage = stage
        self.calls = 0
        self._passes = passes
        self._error_code = error_code
        self._quality = quality
        self._cost = Decimal(cost)

    async def draft(self, sar_input: SarInput) -> AsyncIterator[SarStreamEvent]:
        """Emit one token then the scripted terminal result for this stage."""
        self.calls += 1
        yield SarStreamEvent(type=SarEventType.TOKEN, token=f"{self.stage}-secret ")
        status = SarDraftStatus.DRAFT if self._passes else SarDraftStatus.FAILED
        # A transport failure produces no verdict at all — the gate never saw output to judge —
        # which is exactly what distinguishes it from a quality rejection downstream.
        quality = self._quality
        if quality is None and not (self._error_code and not self._passes):
            quality = _PASSED if self._passes else _REJECTED
        yield SarStreamEvent(
            type=SarEventType.COMPLETED if self._passes else SarEventType.FAILED,
            result=SarDraftResult(
                status=status,
                content=f"{self.stage} draft" if self._passes else "",
                model_id=f"vllm/{self.stage}",
                prompt_version="v2@2.0.0",
                prompt_hash="hash",
                error_code=None if self._passes else (self._error_code or GATE_FAILED_CODE),
                token_usage=SarTokenUsage(output_tokens=10, total_tokens=10),
                cost_usd=self._cost,
                quality=quality,
                attempts=(
                    SarGenerationAttempt(
                        ordinal=0,
                        stage=self.stage,
                        model_id=f"vllm/{self.stage}",
                        connection=f"runpod-{self.stage}",
                        outcome="passed" if self._passes else _OUTCOME_FOR_FAILURE,
                        error_code=None if self._passes else self._error_code,
                        quality=quality,
                        token_usage=SarTokenUsage(output_tokens=10, total_tokens=10),
                        cost_usd=self._cost,
                        prompt_hash="hash",
                    ),
                ),
            ),
        )


def _cascade(*tiers: _ScriptedTier, budget: BudgetGuard | None = None, egress_class=None):
    return QualityGatedSarDrafter(
        tiers=tuple(
            SarCascadeTier(name=tier.stage, drafter=tier, requires_egress_class=egress_class)
            for tier in tiers
        ),
        gate=production_gate(),
        budget=budget or BudgetGuard(),
    )


async def _run(drafter, sar_input) -> list[SarStreamEvent]:
    return [event async for event in drafter.draft(sar_input)]


@pytest.mark.asyncio
async def test_a_passing_first_tier_never_reaches_the_second(make_sar_input) -> None:
    awq, bf16 = _ScriptedTier("awq"), _ScriptedTier("bf16")

    events = await _run(_cascade(awq, bf16), make_sar_input())
    result = events[-1].result

    assert (awq.calls, bf16.calls) == (1, 0)
    assert result.status is SarDraftStatus.DRAFT
    assert result.escalation_tier == 0
    assert result.escalated_from == ()
    assert [event.type for event in events if event.stage is not None] == [
        SarEventType.STAGE_STARTED
    ]


@pytest.mark.asyncio
async def test_a_rejected_first_tier_escalates_to_the_second(make_sar_input) -> None:
    awq, bf16 = _ScriptedTier("awq", passes=False), _ScriptedTier("bf16")

    events = await _run(_cascade(awq, bf16), make_sar_input())
    result = events[-1].result

    assert (awq.calls, bf16.calls) == (1, 1)
    assert result.status is SarDraftStatus.DRAFT
    assert result.escalation_tier == 1
    assert result.escalated_from == ("awq",)
    rejected = [event for event in events if event.type is SarEventType.STAGE_REJECTED]
    assert rejected[0].stage.stage == "awq"
    assert SarGateReason.CITATION_FABRICATED in rejected[0].stage.reasons
    escalated = [event for event in events if event.type is SarEventType.ESCALATED]
    assert [event.stage.stage for event in escalated] == ["bf16"]
    assert [event.stage.ordinal for event in escalated] == [1]


@pytest.mark.asyncio
async def test_rejected_tier_tokens_never_reach_a_client(make_sar_input) -> None:
    """The cascade buffers: text a rejected AWQ draft produced is not a disclosable artifact."""
    events = await _run(
        _cascade(_ScriptedTier("awq", passes=False), _ScriptedTier("bf16")), make_sar_input()
    )

    streamed = "".join(event.token or "" for event in events)
    assert "awq-secret" not in streamed
    assert "bf16-secret" not in streamed
    assert streamed == events[-1].result.content


@pytest.mark.asyncio
async def test_an_exhausted_cascade_fails_explicitly_with_its_verdict(make_sar_input) -> None:
    tiers = (
        _ScriptedTier("awq", passes=False),
        _ScriptedTier("bf16", passes=False),
        _ScriptedTier("external", passes=False),
    )

    events = await _run(_cascade(*tiers), make_sar_input())
    result = events[-1].result

    assert [tier.calls for tier in tiers] == [1, 1, 1]
    assert events[-2].type is SarEventType.CASCADE_FAILED
    assert events[-1].type is SarEventType.FAILED
    assert result.status is SarDraftStatus.FAILED
    assert result.error_code == GATE_FAILED_CODE
    assert result.quality is not None and result.quality.passed is False
    assert result.escalated_from == ("awq", "bf16", "external")


@pytest.mark.asyncio
async def test_attempts_costs_and_usage_aggregate_exactly(make_sar_input) -> None:
    result = (
        await _run(
            _cascade(_ScriptedTier("awq", passes=False), _ScriptedTier("bf16")), make_sar_input()
        )
    )[-1].result

    assert [attempt.ordinal for attempt in result.attempts] == [0, 1]
    assert [attempt.stage for attempt in result.attempts] == ["awq", "bf16"]
    assert result.cost_usd == Decimal("0.002")
    assert result.token_usage.total_tokens == 20
    assert result.fallback_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_code", ["egress_source_not_allowed", "llm_policy_denied", "sar_guardrail_blocked"]
)
async def test_a_non_escalatable_refusal_stops_the_cascade(make_sar_input, error_code) -> None:
    """These fail identically at every tier, so spending again would buy nothing."""
    awq = _ScriptedTier("awq", passes=False, error_code=error_code, quality=None)
    bf16 = _ScriptedTier("bf16")

    result = (await _run(_cascade(awq, bf16), make_sar_input()))[-1].result

    assert bf16.calls == 0
    assert result.status is SarDraftStatus.FAILED


@pytest.mark.asyncio
async def test_a_terminal_verdict_stops_the_cascade(make_sar_input) -> None:
    awq = _ScriptedTier("awq", passes=False, quality=_TERMINAL)
    bf16 = _ScriptedTier("bf16")

    await _run(_cascade(awq, bf16), make_sar_input())

    assert bf16.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("error_code", ["llm_timeout", "llm_rate_limited", "llm_empty_output"])
async def test_a_transport_failure_escalates_and_stays_distinct_from_a_gate_failure(
    make_sar_input, error_code: str
) -> None:
    """A timeout is not a quality verdict: it escalates, and its code survives on the attempt."""
    awq = _ScriptedTier("awq", passes=False, error_code=error_code, quality=None)
    bf16 = _ScriptedTier("bf16")

    events = await _run(_cascade(awq, bf16), make_sar_input())
    result = events[-1].result

    assert bf16.calls == 1
    assert result.status is SarDraftStatus.DRAFT
    assert result.attempts[0].error_code == error_code
    assert result.attempts[0].error_code != GATE_FAILED_CODE
    assert result.attempts[0].quality is None
    rejected = [event for event in events if event.type is SarEventType.STAGE_REJECTED]
    assert rejected[0].stage.error_code == error_code
    assert rejected[0].stage.reasons == ()


@pytest.mark.asyncio
async def test_preflight_rejects_an_ineligible_case_with_zero_model_calls(make_sar_input) -> None:
    awq = _ScriptedTier("awq")

    events = await _run(_cascade(awq), make_sar_input(source="api-upload"))

    assert awq.calls == 0
    assert events[-1].type is SarEventType.FAILED
    assert events[-1].result.error_code == "egress_source_not_allowed"


@pytest.mark.asyncio
async def test_a_stage_requiring_an_egress_class_is_skipped_when_it_is_absent(
    make_sar_input,
) -> None:
    external = _ScriptedTier("external")
    drafter = QualityGatedSarDrafter(
        tiers=(
            SarCascadeTier(name="external", drafter=external, requires_egress_class="deidentified"),
        ),
        gate=production_gate(),
        budget=BudgetGuard(),
    )

    events = await _run(drafter, make_sar_input())

    assert external.calls == 0
    rejected = [event for event in events if event.type is SarEventType.STAGE_REJECTED]
    assert rejected[0].stage.error_code == EGRESS_TIER_NOT_ALLOWED


@pytest.mark.asyncio
async def test_the_budget_is_checked_before_every_tier(make_sar_input) -> None:
    guard = BudgetGuard(session_limit_usd=Decimal("0.0005"))
    awq = _ScriptedTier("awq")

    with pytest.raises(SarBudgetExceededError):
        await _run(_cascade(awq, budget=_spent(guard)), make_sar_input())

    assert awq.calls == 0


def _spent(guard: BudgetGuard) -> BudgetGuard:
    """Return the guard with its session cap already consumed."""
    guard.record(Decimal("0.01"))
    return guard


@pytest.mark.asyncio
async def test_preflight_rejects_a_case_with_no_evidence_before_any_tier(
    make_sar_input, monkeypatch
) -> None:
    """A catalog with no facts cannot support any claim, so no tier may be charged for it."""
    awq = _ScriptedTier("awq")
    monkeypatch.setattr(
        drafter_gated, "build_evidence_catalog", lambda _input: SarEvidenceCatalog()
    )

    events = await _run(_cascade(awq), make_sar_input())

    assert awq.calls == 0
    assert events[-1].result.error_code == GATE_FAILED_CODE
    assert SarGateReason.EVIDENCE_EMPTY in events[-1].result.quality.reasons


@pytest.mark.asyncio
async def test_a_tier_that_yields_no_terminal_event_fails_that_stage(make_sar_input) -> None:
    """A misbehaving drafter degrades to a stage failure rather than hanging the cascade."""

    class _Silent:
        stage = "awq"

        async def draft(self, _sar_input):
            return
            yield  # pragma: no cover - the generator never yields

    drafter = QualityGatedSarDrafter(
        tiers=(SarCascadeTier(name="awq", drafter=_Silent()),),
        gate=production_gate(),
        budget=BudgetGuard(),
    )

    events = await _run(drafter, make_sar_input())

    assert events[-1].result.status is SarDraftStatus.FAILED
    assert events[-1].result.error_code == "sar_cascade_no_result"


def test_a_cascade_longer_than_the_policy_maximum_is_refused() -> None:
    gate = production_gate()
    tiers = tuple(
        SarCascadeTier(name=f"tier-{index}", drafter=_ScriptedTier(f"tier-{index}"))
        for index in range(gate.policy.max_tiers + 1)
    )

    with pytest.raises(SarCascadeConfigError):
        QualityGatedSarDrafter(tiers=tiers, gate=gate, budget=BudgetGuard())


def test_an_empty_or_ambiguous_cascade_is_refused() -> None:
    gate, budget = production_gate(), BudgetGuard()
    duplicated = (
        SarCascadeTier(name="awq", drafter=_ScriptedTier("awq")),
        SarCascadeTier(name="awq", drafter=_ScriptedTier("awq")),
    )

    with pytest.raises(SarCascadeConfigError):
        QualityGatedSarDrafter(tiers=(), gate=gate, budget=budget)
    with pytest.raises(SarCascadeConfigError):
        QualityGatedSarDrafter(tiers=duplicated, gate=gate, budget=budget)


@pytest.mark.asyncio
async def test_two_failed_tiers_escalate_to_the_external_stage(make_sar_input) -> None:
    """Scenario 3: the full AWQ → BF16 → external cascade, served by its last stage."""
    awq = _ScriptedTier("awq", passes=False)
    bf16 = _ScriptedTier("bf16", passes=False)
    external = _ScriptedTier("external")

    events = await _run(_cascade(awq, bf16, external), make_sar_input())
    result = events[-1].result

    assert [tier.calls for tier in (awq, bf16, external)] == [1, 1, 1]
    assert result.status is SarDraftStatus.DRAFT
    assert result.escalation_tier == 2
    assert result.escalated_from == ("awq", "bf16")
    assert [event.stage.stage for event in events if event.type is SarEventType.ESCALATED] == [
        "bf16",
        "external",
    ]
    streamed = "".join(event.token or "" for event in events)
    assert "awq-secret" not in streamed
    assert "bf16-secret" not in streamed


@pytest.mark.asyncio
async def test_malformed_tier_output_escalates_as_a_schema_rejection(make_sar_input) -> None:
    """Scenario 5: unparseable output is a gate verdict, not a transport error, and escalates."""
    awq = _ScriptedTier("awq", passes=False, quality=_SCHEMA_INVALID)
    bf16 = _ScriptedTier("bf16")

    events = await _run(_cascade(awq, bf16), make_sar_input())
    result = events[-1].result

    assert bf16.calls == 1
    assert result.status is SarDraftStatus.DRAFT
    assert result.escalation_tier == 1
    rejected = [event for event in events if event.type is SarEventType.STAGE_REJECTED]
    assert rejected[0].stage.reasons == (SarGateReason.SCHEMA_INVALID,)
    assert result.attempts[0].quality is not None
    assert result.attempts[0].quality.reasons == (SarGateReason.SCHEMA_INVALID,)


@pytest.mark.asyncio
async def test_preflight_rejects_an_uncitable_case_before_any_tier(make_sar_input) -> None:
    """Retrieval returned nothing, so the cascade refuses the case instead of paying three tiers."""
    awq, bf16, external = (
        _ScriptedTier("awq"),
        _ScriptedTier("bf16"),
        _ScriptedTier("external"),
    )

    events = await _run(_cascade(awq, bf16, external), make_sar_input(citations=()))
    result = events[-1].result

    assert [tier.calls for tier in (awq, bf16, external)] == [0, 0, 0]
    assert result.status is SarDraftStatus.FAILED
    assert result.error_code == GATE_FAILED_CODE
    assert result.quality is not None
    assert result.quality.reasons == (SarGateReason.NO_CITATIONS,)
    assert result.quality.fallback_required is False
    assert result.cost_usd == Decimal("0")
    assert result.attempts == ()


@pytest.mark.asyncio
async def test_a_case_with_citations_still_reaches_the_first_tier(make_sar_input) -> None:
    """The preflight refuses only what no tier could serve; a normal case is unaffected."""
    awq = _ScriptedTier("awq")

    events = await _run(_cascade(awq), make_sar_input())

    assert awq.calls == 1
    assert events[-1].result.status is SarDraftStatus.DRAFT
