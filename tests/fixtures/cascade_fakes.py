"""Deterministic fixtures for the protocol-v2 cascade harness: attempts, corpora, and drafters.

A cascade scenario drives the PRODUCTION drafter, so the fixture that stands in for it is a
`SarDrafter` that returns real `SarDraftResult`s with real `SarGenerationAttempt` rows — not a
stub that returns metrics. That keeps the recorded evidence (stage names, gate verdicts, token
usage, escalation tier) shaped exactly like a live run's, so a test cannot pass against a shape the
product never produces. Nothing here performs socket, GPU, provider, or database IO.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sar_inputs import build_sar_input
from vllm_bench_fakes import HASH, NOW, benchmark_case, measurement, sar_response, server

from fraudlens_ml.evaluation import CitationMetrics
from fraudlens_ml.sar import (
    DeterministicReviewChecks,
    SarDraftContent,
    SarDraftResult,
    SarDraftStatus,
    SarEventType,
    SarGenerationAttempt,
    SarInput,
    SarQualityGateResult,
    SarStreamEvent,
    SarTokenUsage,
)
from lib.experiments.budget import BudgetConfig, LedgerEntry, load_budget_config
from lib.study import sha256_hex
from lib.vllm_bench.config import VllmBenchConfig
from lib.vllm_bench.load import ordered_cases
from lib.vllm_bench.state import (
    BenchmarkCase,
    CaseArtifact,
    LevelCheckpoint,
    RequestMeasurement,
    RunManifest,
    TokenUsage,
    case_artifact_sha256,
)
from lib.vllm_bench.telemetry import TelemetrySample

_REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_POLICY_VERSION = "sar-gate-replay-v1"
_CLEAN_CHECKS = DeterministicReviewChecks(
    passed=True,
    every_claim_has_evidence=True,
    cited_ids_are_available=True,
    evidence_refs_are_available=True,
)
_CLEAN_METRICS = CitationMetrics(
    precision=1.0, recall=1.0, produced_count=1, valid_count=1, expected_count=1, recalled_count=1
)
POLICY_HASH = "c" * 64


def attempt(
    case_id: str,
    ordinal: int,
    stage: str,
    *,
    passed: bool,
    reasons: tuple[str, ...] = (),
    latency_s: float = 1.0,
    sequence: int = 0,
    cost_usd: Decimal | None = None,
) -> RequestMeasurement:
    """Build one recorded cascade attempt exactly as a scenario level would persist it."""
    return RequestMeasurement(
        case_id=case_id,
        sequence=sequence,
        seed=1700 + sequence,
        attempts=1,
        started_at=NOW,
        latency_s=latency_s,
        content=sar_response() if passed else "",
        finish_reason="stop",
        usage=TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
        stage=stage,
        attempt_ordinal=ordinal,
        gate_passed=passed,
        gate_reasons=() if passed else reasons,
        cost_usd=cost_usd,
    )


def _case_with_input(case_id: str, case_set: str = "measured") -> BenchmarkCase:
    """Build one benchmark case carrying the production SAR input a cascade drafts from."""
    case = benchmark_case(case_id, case_set)  # type: ignore[arg-type]
    return case.model_copy(update={"sar_input": build_sar_input(transaction_id=case_id)})


def cascade_config(config: VllmBenchConfig) -> VllmBenchConfig:
    """Shrink the declared scenario matrix onto the levels the small fixture run measures."""
    measured = config.load.concurrency_levels
    scenarios = tuple(
        scenario.model_copy(
            update={
                "concurrency_levels": (
                    measured if len(scenario.concurrency_levels) > 1 else measured[-1:]
                )
            }
        )
        for scenario in config.cascade.scenarios
    )
    return config.model_copy(
        update={"cascade": config.cascade.model_copy(update={"scenarios": scenarios})}
    )


def cascade_artifact(config: VllmBenchConfig) -> CaseArtifact:
    """Build a small protocol-v2 corpus whose cases carry production SAR inputs."""
    return CaseArtifact(
        protocol_version=config.protocol_version,
        profile="full",
        case_source="ibm-final-test",
        config_sha256=config.config_sha256,
        upstream_sha256=HASH,
        prompt_version="v2",
        prompt_sha256=HASH,
        distinct_subjects=2,
        subject_overlap=0,
        cases=(
            _case_with_input("case-0"),
            _case_with_input("case-1"),
            _case_with_input("warmup-0", "warmup"),
            _case_with_input("abstention-0", "abstention"),
        ),
    )


def _attempt_row(
    ordinal: int, stage: str, *, passed: bool, error: bool = False
) -> SarGenerationAttempt:
    """Build one production attempt row with its recorded gate verdict or transport failure."""
    if error:
        return SarGenerationAttempt(
            ordinal=ordinal,
            stage=stage,
            model_id=f"vllm/{stage}",
            connection=f"runpod-{stage}",
            outcome="failed",
            error_code="provider_unavailable",
            latency_ms=1000,
            prompt_hash=HASH,
            policy_hash=POLICY_HASH,
        )
    return SarGenerationAttempt(
        ordinal=ordinal,
        stage=stage,
        model_id=f"vllm/{stage}",
        connection=f"runpod-{stage}",
        outcome="served" if passed else "rejected",
        error_code=None if passed else "sar_quality_gate_failed",
        quality=SarQualityGateResult(
            passed=passed,
            policy_version=GATE_POLICY_VERSION,
            policy_hash=POLICY_HASH,
            reasons=() if passed else ("citation_fabricated",),
            checks=_CLEAN_CHECKS,
            citation_metrics=_CLEAN_METRICS,
            fallback_required=not passed,
        ),
        latency_ms=1000,
        token_usage=SarTokenUsage(input_tokens=10, output_tokens=10, total_tokens=20),
        cost_usd=Decimal("0.001"),
        prompt_hash=HASH,
        policy_hash=POLICY_HASH,
    )


class ScriptedCascadeDrafter:
    """A `SarDrafter` that returns the attempt trail a real cascade would have recorded."""

    def __init__(
        self,
        *,
        stages: tuple[str, ...] = ("awq", "bf16"),
        escalate: Iterable[str] = (),
        no_terminal: Iterable[str] = (),
        transport_error: Iterable[str] = (),
    ) -> None:
        """Bind which cases escalate, fail transport, or never produce a terminal event."""
        self._stages = stages
        self._escalate = set(escalate)
        self._no_terminal = set(no_terminal)
        self._transport_error = set(transport_error)
        self.calls = 0
        self.warmups = 0

    async def draft(self, sar_input: SarInput) -> AsyncIterator[SarStreamEvent]:
        """Stream the events the cascade emits, ending in one terminal result."""
        case_id = sar_input.transaction_id
        if case_id.startswith("warmup"):
            self.warmups += 1
        else:
            self.calls += 1
        if case_id in self._no_terminal:
            yield SarStreamEvent(type=SarEventType.TOKEN, token="partial")
            return
        escalated = case_id in self._escalate
        first = _attempt_row(
            0, self._stages[0], passed=False, error=case_id in self._transport_error
        )
        attempts = (
            (first, _attempt_row(1, self._stages[1], passed=True))
            if escalated
            else (_attempt_row(0, self._stages[0], passed=True),)
        )
        yield SarStreamEvent(type=SarEventType.TOKEN, token="Suspicious activity.")
        yield SarStreamEvent(
            type=SarEventType.COMPLETED,
            result=SarDraftResult(
                status=SarDraftStatus.DRAFT,
                content="Suspicious activity.",
                structured=SarDraftContent(
                    subject="Structuring",
                    narrative="The transaction shows structuring indicators.",
                    recommended_action="Escalate for human review.",
                ),
                model_id=attempts[-1].model_id,
                prompt_version="v2",
                prompt_hash=HASH,
                attempts=attempts,
                escalation_tier=len(attempts) - 1,
                quality=attempts[-1].quality,
            ),
        )


class BrokenSampler:
    """A GPU sampler that never answers, standing in for one endpoint losing telemetry."""

    async def sample(self) -> TelemetrySample:
        """Fail exactly as an unreachable Pod's sampler command does."""
        raise OSError("sampler unreachable")


def replay_manifest(config: VllmBenchConfig) -> tuple[CaseArtifact, RunManifest]:
    """Build a two-arm persisted run where one case fails AWQ and passes BF16."""
    artifact = cascade_artifact(config)
    case_hash = case_artifact_sha256(artifact)
    levels: dict[str, LevelCheckpoint] = {}
    for arm in ("bf16", "awq"):
        for concurrency in config.load.concurrency_levels:
            ordered = ordered_cases(artifact, config, profile="full", concurrency=concurrency)
            measurements = []
            for index, case in enumerate(ordered):
                observed = measurement(case, index, latency_s=1.0 if arm == "awq" else 2.0)
                if arm == "awq" and case.case_id == "case-1":
                    observed = observed.model_copy(
                        update={"content": sar_response(fabricated=True)}
                    )
                measurements.append(observed)
            levels[f"{arm}:{concurrency}"] = LevelCheckpoint(
                arm=arm,
                concurrency=concurrency,
                case_order_sha256=sha256_hex("\n".join(case.case_id for case in ordered)),
                cases_sha256=case_hash,
                started_at=NOW,
                completed_at=NOW + timedelta(seconds=2),
                warmup_completed=1,
                measurements=tuple(measurements),
            )
    manifest = RunManifest(
        run_id="vllm-bench-0123456789abcdef",
        protocol_version=config.protocol_version,
        profile="full",
        config_sha256=config.config_sha256,
        cases_sha256=case_hash,
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=10),
        servers={arm: server(config, arm) for arm in ("bf16", "awq")},
        levels=levels,
    )
    return artifact, manifest


def budget_config() -> BudgetConfig:
    """Load the committed experiment budget the pilot admits its matrix against."""
    return load_budget_config(_REPO_ROOT)


def ledger_row(run_id: str, hours: Decimal = Decimal("1.0")) -> tuple[LedgerEntry, ...]:
    """Build the single billed resource session a replay pilot derives its overhead from."""
    return (
        LedgerEntry(
            session_date=date(2026, 9, 14),
            provider="runpod",
            sku="NVIDIA GeForce RTX 4090",
            purchase_option="pay_as_you_go",
            started_at="2026-09-14T21:59:09Z",
            stopped_at="2026-09-15T04:01:53Z",
            hours=hours,
            quoted_rate_usd=Decimal("0.74"),
            projected_cost_usd=Decimal("5.92"),
            actual_cost_usd=None,
            run_id=run_id,
            budget_scope="current-plan",
            allocation="gpu_benchmark",
            teardown_verified="yes",
        ),
    )
