"""Summary: Typed, deterministic fixtures for the provider-free vLLM benchmark suite.

Key classes:
- FakeStreamClient: concurrency-recording async benchmark client.
- FakeSampler: deterministic telemetry sampler.

Key functions:
- replay_gate: the committed gate policy these prompt-v1-shaped fixtures are judged under.
- benchmark_case: build one synthetic measured or abstention case.
- complete_benchmark: build a small accepted full-profile artifact and run manifest.

Notes:
- All fixtures are synthetic and perform no socket, GPU, cloud, or database IO.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

from fraudlens_backend.sar.quality_gate import (
    REPLAY_GATE_POLICY,
    SarQualityGate,
    load_sar_gate_policy,
)
from lib.study import sha256_hex
from lib.vllm_bench.config import ArmName, VllmBenchConfig
from lib.vllm_bench.load import ordered_cases
from lib.vllm_bench.state import (
    BenchmarkCase,
    BenchmarkMessage,
    CaseArtifact,
    CaseSet,
    LevelCheckpoint,
    RequestMeasurement,
    RunManifest,
    ServerProvenance,
    TokenUsage,
    case_artifact_sha256,
)
from lib.vllm_bench.telemetry import TelemetrySample

HASH = "a" * 64
RUN_ID = "vllm-bench-0123456789abcdef"
NOW = datetime(2026, 9, 13, tzinfo=UTC)


def replay_gate() -> SarQualityGate:
    """Return the committed policy these prompt-v1-shaped fixtures are judged under.

    The fixtures below carry no asserted facts and no FinCEN sections, because that is exactly the
    shape the persisted benchmark corpus has. Judging them by the production runtime policy would
    measure the fixture rather than the metric under test; the production policy is exercised by
    the backend gate suites, which use production-shaped drafts.
    """
    return SarQualityGate(load_sar_gate_policy(policy=REPLAY_GATE_POLICY))


def benchmark_case(case_id: str = "case-0", case_set: CaseSet = "measured") -> BenchmarkCase:
    """Build one closed-vocabulary synthetic benchmark case."""
    abstention = case_set == "abstention"
    return BenchmarkCase(
        case_id=case_id,
        case_set=case_set,
        source_dataset="fixture",
        data_class="synthetic",
        source="ibm-aml-synthetic",
        messages=(
            BenchmarkMessage(role="system", content="policy"),
            BenchmarkMessage(role="system", content="schema"),
            BenchmarkMessage(role="user", content="synthetic facts"),
        ),
        required_facts=() if abstention else ("100 USD", "2024-01-01", "structuring"),
        offered_citation_ids=() if abstention else ("31 CFR 1010.314",),
        expected_citation_ids=() if abstention else ("31 CFR 1010.314",),
        available_evidence_refs=() if abstention else ("case-evidence-transaction",),
        payment_format="wire",
        amount_band="under-1k",
        history_length_band="short",
        prompt_length_band="short",
        prompt_chars=100,
    )


def sar_response(*, abstention: bool = False, fabricated: bool = False) -> str:
    """Return one schema-valid grounded response or an evidence-free abstention."""
    citation = "99 FAKE 1" if fabricated else "31 CFR 1010.314"
    claims = []
    cited: list[str] = []
    if not abstention:
        cited = [citation]
        claims = [
            {
                "statement": "100 USD on 2024-01-01 showed structuring.",
                "evidenceRefs": ["case-evidence-transaction"],
                "citationIds": [citation],
            }
        ]
    return json.dumps(
        {
            "subject": "Synthetic review",
            "narrative": "100 USD on 2024-01-01 showed structuring.",
            "claims": claims,
            "sections": [],
            "citedRegulations": cited,
            "recommendedAction": "Escalate for human review.",
        }
    )


def measurement(
    case: BenchmarkCase,
    sequence: int,
    *,
    latency_s: float = 1.0,
    attempts: int = 1,
    error_code: str | None = None,
) -> RequestMeasurement:
    """Build a successful or terminal-error request observation."""
    if error_code is not None:
        return RequestMeasurement(
            case_id=case.case_id,
            sequence=sequence,
            seed=1700 + sequence,
            attempts=attempts,
            started_at=NOW,
            latency_s=latency_s,
            error_code=error_code,
        )
    return RequestMeasurement(
        case_id=case.case_id,
        sequence=sequence,
        seed=1700 + sequence,
        attempts=attempts,
        started_at=NOW,
        latency_s=latency_s,
        ttft_s=latency_s / 4,
        content=sar_response(abstention=case.case_set == "abstention"),
        finish_reason="stop",
        usage=TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
    )


def telemetry() -> TelemetrySample:
    """Build one complete GPU and scheduler observation."""
    return TelemetrySample(
        captured_at=NOW,
        gpu_utilization_pct=75,
        memory_used_mib=12_000,
        memory_total_mib=24_000,
        kv_cache_usage_pct=40,
        requests_running=2,
        requests_waiting=0,
    )


def server(config: VllmBenchConfig, arm: ArmName) -> ServerProvenance:
    """Build pinned test provenance with an accepted AWQ memory reduction."""
    selected = config.arms[arm]
    host = config.cost.hosts[config.cost.default_host]
    price_key = next(iter(host.prices))
    return ServerProvenance(
        arm=arm,
        model=selected.model,
        model_revision=selected.revision,
        tokenizer=selected.tokenizer,
        tokenizer_revision=selected.tokenizer_revision,
        image=f"{config.server.image}:{config.server.image_tag}",
        image_digest=f"sha256:{'b' * 64}",
        vllm_version=config.server.image_tag,
        gpu_name="Synthetic GPU",
        driver_version="test-driver",
        host_key=config.cost.default_host,
        provider=host.provider,
        sku=host.sku,
        region=host.region,
        purchase_option=price_key,
        hourly_rate_usd=float(host.prices[price_key]),
        price_source_url=host.price_source_url,
        price_verified_at=host.price_verified_at.isoformat(),
        weight_memory_gib=14.0 if arm == "bf16" else 5.0,
        safetensors_total_gib=float(selected.safetensors_total_gib),
        kv_cache_tokens=10_000 if arm == "bf16" else 20_000,
        maximum_concurrency=4,
    )


def small_config(config: VllmBenchConfig) -> VllmBenchConfig:
    """Shrink full-profile counts and levels while preserving the protocol hash."""
    cases = config.cases.model_copy(update={"count": 2, "quotas": {"fixture": 2}})
    load = config.load.model_copy(
        update={"concurrency_levels": (1, 2), "warmup_requests": 1, "cooldown_s": 0}
    )
    return config.model_copy(update={"cases": cases, "load": load})


def complete_benchmark(
    config: VllmBenchConfig,
) -> tuple[VllmBenchConfig, CaseArtifact, RunManifest]:
    """Build a complete accepted two-arm/two-level full-profile benchmark."""
    config = small_config(config)
    measured = (benchmark_case("case-0"), benchmark_case("case-1"))
    warmup = benchmark_case("warmup-0", "warmup")
    abstention = benchmark_case("abstention-0", "abstention")
    artifact = CaseArtifact(
        protocol_version=config.protocol_version,
        profile="full",
        case_source="ibm-final-test",
        config_sha256=config.config_sha256,
        upstream_sha256=HASH,
        prompt_version="v1",
        prompt_sha256=HASH,
        distinct_subjects=2,
        subject_overlap=0,
        cases=(*measured, warmup, abstention),
    )
    levels: dict[str, LevelCheckpoint] = {}
    case_hash = case_artifact_sha256(artifact)
    for arm in ("bf16", "awq"):
        for concurrency in (1, 2):
            ordered = ordered_cases(
                artifact,
                config,
                profile="full",
                concurrency=concurrency,
            )
            order_hash = sha256_hex("\n".join(case.case_id for case in ordered))
            levels[f"{arm}:{concurrency}"] = LevelCheckpoint(
                arm=arm,
                concurrency=concurrency,
                case_order_sha256=order_hash,
                cases_sha256=case_hash,
                started_at=NOW,
                completed_at=NOW + timedelta(seconds=2),
                warmup_completed=1,
                measurements=tuple(measurement(case, index) for index, case in enumerate(ordered)),
                quality_measurements=(measurement(abstention, 0),) if concurrency == 1 else (),
                telemetry=(telemetry(),),
            )
    manifest = RunManifest(
        run_id=RUN_ID,
        protocol_version=config.protocol_version,
        profile="full",
        config_sha256=config.config_sha256,
        cases_sha256=case_hash,
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=10),
        servers={arm: server(config, arm) for arm in ("bf16", "awq")},
        levels=levels,
    )
    return config, artifact, manifest


class FakeStreamClient:
    """Return typed observations while recording actual in-flight concurrency."""

    def __init__(self) -> None:
        self.active = 0
        self.maximum_active = 0
        self.calls: list[str] = []

    async def generate(
        self, case: BenchmarkCase, *, sequence: int, seed: int
    ) -> RequestMeasurement:
        """Record the call and yield once so the semaphore can expose parallelism."""
        del seed
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        self.calls.append(case.case_id)
        await asyncio.sleep(0)
        self.active -= 1
        return measurement(case, sequence)


class FakeSampler:
    """Return one deterministic telemetry sample."""

    async def sample(self) -> TelemetrySample:
        """Return a typed sample without IO."""
        return telemetry()
