"""Summary: Typed case artifacts and resumable per-arm/per-level benchmark checkpoints.

Key classes:
- BenchmarkMessage: one exact OpenAI-compatible chat message.
- BenchmarkCase: one synthetic, egress-approved measured, development, warm-up, or abstention case.
- CaseExclusion: one context-limit exclusion without sensitive prompt content.
- CaseArtifact: hash-bound deterministic case corpus.
- CaseFileManifest: preparation-time SHA and lineage for one local case artifact.
- CaseReleaseManifest: metadata binding one release-safe JSONL gzip corpus.
- TokenUsage: required server-reported token accounting.
- RequestMeasurement: one terminal streamed-request observation.
- ServerProvenance: immutable model, image, GPU, host, and startup-log provenance.
- LevelCheckpoint: one complete arm/concurrency result written atomically.
- RunManifest: resumable run state containing only completed levels.

Key functions:
- case_artifact_sha256: hash canonical case-artifact bytes.
- write_case_artifact: atomically persist a case artifact without a sidecar.
- load_case_artifact: parse one case artifact and return its byte SHA.
- case_manifest_path: derive the stable preparation-time SHA sidecar path.
- write_case_bundle: atomically persist a deterministic corpus and SHA sidecar.
- load_case_bundle: parse and verify a case artifact against its SHA sidecar.
- initialize_run: create or validate one resumable run manifest.
- write_run: atomically persist completed checkpoint state.
- load_run: parse one run manifest.
- validate_restart_memory: enforce the one-percent restart weight-memory tolerance.

Notes:
- Partial levels are never represented; a level enters the manifest only after every request ends.
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from lib.study import (
    atomic_write_model,
    canonical_json,
    install_bound_artifacts,
    sha256_hex,
)
from lib.vllm_bench.config import ArmName, PurchaseOption, VllmBenchConfig
from lib.vllm_bench.telemetry import TelemetrySample

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    extra="forbid",
    alias_generator=to_camel,
    populate_by_name=True,
    protected_namespaces=(),
)
_HASH = r"^[0-9a-f]{64}$"
CaseSet = Literal["measured", "development", "warmup", "abstention"]
ChatRole = Literal["system", "user", "assistant"]


class BenchmarkMessage(BaseModel):
    """One exact chat message transmitted to the benchmark endpoint."""

    model_config = _MODEL_CONFIG

    role: ChatRole = Field(..., description="Chat role.")
    content: str = Field(..., min_length=1, description="PHI-masked message content.")


class BenchmarkCase(BaseModel):
    """One deterministic synthetic SAR benchmark request and its quality expectations."""

    model_config = _MODEL_CONFIG

    case_id: str = Field(..., min_length=1, description="Stable non-account case identity.")
    case_set: CaseSet = Field(..., description="Measured, development, warm-up, or abstention set.")
    source_dataset: str = Field(..., min_length=1, description="IBM candidate or SAR fixture.")
    data_class: Literal["synthetic"] = Field(..., description="Only permitted data class.")
    source: Literal["ibm-aml-synthetic"] = Field(..., description="Egress-approved provenance.")
    messages: tuple[BenchmarkMessage, ...] = Field(
        ..., min_length=3, description="Exact policy, template, and case messages."
    )
    required_facts: tuple[str, ...] = Field(..., description="Facts expected in valid output.")
    offered_citation_ids: tuple[str, ...] = Field(..., description="Closed citation vocabulary.")
    expected_citation_ids: tuple[str, ...] = Field(..., description="Expected relevant citations.")
    available_evidence_refs: tuple[str, ...] = Field(
        ..., description="Closed evidence-reference vocabulary."
    )
    payment_format: str = Field(..., min_length=1, description="Stratification category.")
    amount_band: str = Field(..., min_length=1, description="Stratification category.")
    history_length_band: str = Field(..., min_length=1, description="Stratification category.")
    prompt_length_band: str = Field(..., min_length=1, description="Stratification category.")
    prompt_chars: int = Field(..., gt=0, description="Exact outbound prompt character count.")

    @model_validator(mode="after")
    def _closed_expectations(self) -> BenchmarkCase:
        if self.case_set == "abstention" and (
            self.offered_citation_ids or self.expected_citation_ids or self.available_evidence_refs
        ):
            raise ValueError("abstention cases must remove citations and evidence")
        if not set(self.expected_citation_ids).issubset(self.offered_citation_ids):
            raise ValueError("expected citations must be a subset of offered citations")
        return self


class CaseExclusion(BaseModel):
    """One case excluded before execution because its exact prompt exceeded context policy."""

    model_config = _MODEL_CONFIG

    source_dataset: str = Field(..., min_length=1, description="Candidate source.")
    reason: Literal["context_limit"] = Field(..., description="Stable exclusion reason.")
    prompt_chars: int = Field(..., gt=0, description="Observed outbound prompt characters.")


class CaseArtifact(BaseModel):
    """Deterministic, hash-bound synthetic benchmark case corpus."""

    model_config = _MODEL_CONFIG

    protocol_version: str = Field(..., min_length=1, description="Benchmark protocol version.")
    profile: str = Field(..., min_length=1, description="Workload profile used for generation.")
    case_source: str = Field(..., min_length=1, description="IBM or SAR-eval builder.")
    config_sha256: str = Field(..., pattern=_HASH, description="Exact benchmark config hash.")
    upstream_sha256: str = Field(..., pattern=_HASH, description="Source protocol/artifact hash.")
    prompt_version: str = Field(..., min_length=1, description="Production SAR prompt version.")
    prompt_sha256: str = Field(..., pattern=_HASH, description="Exact production prompt hash.")
    distinct_subjects: int = Field(..., ge=0, description="Distinct selected subject accounts.")
    subject_overlap: int = Field(..., ge=0, description="Repeated subject selections.")
    cases: tuple[BenchmarkCase, ...] = Field(..., min_length=1, description="All case sets.")
    exclusions: tuple[CaseExclusion, ...] = Field(default=(), description="Context exclusions.")

    @model_validator(mode="after")
    def _unique_and_synthetic(self) -> CaseArtifact:
        identifiers = [case.case_id for case in self.cases]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("benchmark case ids must be unique")
        if any(case.data_class != "synthetic" for case in self.cases):
            raise ValueError("benchmark cases must be synthetic")
        if self.distinct_subjects + self.subject_overlap < len(
            [case for case in self.cases if case.case_set == "measured"]
        ):
            raise ValueError("subject accounting cannot be smaller than measured cases")
        return self


class CaseFileManifest(BaseModel):
    """Preparation-time byte hash and lineage for one local case artifact."""

    model_config = _MODEL_CONFIG

    artifact_sha256: str = Field(..., pattern=_HASH, description="Exact case-file byte hash.")
    config_sha256: str = Field(..., pattern=_HASH, description="Benchmark config hash.")
    upstream_sha256: str = Field(..., pattern=_HASH, description="Source lineage hash.")
    prompt_sha256: str = Field(..., pattern=_HASH, description="Production prompt hash.")
    case_source: str = Field(..., min_length=1, description="Case builder source.")
    profile: str = Field(..., min_length=1, description="Workload profile.")
    cases: int = Field(..., gt=0, description="Total case records.")
    measured_cases: int = Field(..., gt=0, description="Measured case records.")


class CaseReleaseManifest(BaseModel):
    """Hash and lineage metadata for one release-attached compressed case corpus."""

    model_config = _MODEL_CONFIG

    archive_name: str = Field(..., min_length=1, description="Compressed JSONL asset name.")
    archive_sha256: str = Field(..., pattern=_HASH, description="Exact archive byte hash.")
    artifact_sha256: str = Field(..., pattern=_HASH, description="Source case-artifact hash.")
    config_sha256: str = Field(..., pattern=_HASH, description="Benchmark config hash.")
    upstream_sha256: str = Field(..., pattern=_HASH, description="Phase-6/source lineage hash.")
    case_source: str = Field(..., min_length=1, description="Case builder source.")
    profile: Literal["full"] = Field(..., description="Only the full corpus is releasable.")
    cases: int = Field(..., gt=0, description="Total JSONL records in the archive.")
    measured_cases: int = Field(..., gt=0, description="Measured records in the archive.")
    license: str = Field(..., min_length=1, description="Dataset derivative license.")
    attribution: str = Field(..., min_length=1, description="Required source attribution.")
    source_url: str = Field(..., min_length=1, description="Authoritative source URL.")


class TokenUsage(BaseModel):
    """Required usage object returned by the terminal stream chunk."""

    model_config = _MODEL_CONFIG

    prompt_tokens: int = Field(..., ge=0, description="Server-counted input tokens.")
    completion_tokens: int = Field(..., ge=0, description="Server-counted generated tokens.")
    total_tokens: int = Field(..., ge=0, description="Server-counted total tokens.")


class RequestMeasurement(BaseModel):
    """One measured terminal request observation, including errors and raw quality input."""

    model_config = _MODEL_CONFIG

    case_id: str = Field(..., min_length=1, description="Case identity.")
    sequence: int = Field(..., ge=0, description="Identical per-level request order.")
    seed: int = Field(..., ge=0, description="Per-request seed.")
    attempts: int = Field(..., ge=1, description="Harness-owned attempt count.")
    started_at: datetime = Field(..., description="UTC request start.")
    latency_s: float = Field(..., ge=0, description="End-to-end stream latency.")
    ttft_s: float | None = Field(default=None, ge=0, description="Time to first content delta.")
    content: str = Field(default="", description="Synthetic SAR output used for local quality.")
    finish_reason: str | None = Field(default=None, description="Provider finish reason.")
    usage: TokenUsage | None = Field(default=None, description="Required usage on success.")
    error_code: str | None = Field(default=None, description="Stable terminal failure category.")

    @model_validator(mode="after")
    def _success_shape(self) -> RequestMeasurement:
        if self.error_code is None and (self.usage is None or self.ttft_s is None):
            raise ValueError("successful measurements require TTFT and token usage")
        if self.error_code is not None and self.usage is not None:
            raise ValueError("failed measurements cannot claim token usage")
        return self


class ServerProvenance(BaseModel):
    """Observed immutable server, host, GPU, and pricing provenance for one arm."""

    model_config = _MODEL_CONFIG

    arm: ArmName = Field(..., description="Benchmark arm.")
    model: str = Field(..., min_length=1, description="Pinned model repository.")
    model_revision: str = Field(..., min_length=1, description="Pinned model commit.")
    tokenizer: str = Field(..., min_length=1, description="Pinned tokenizer repository.")
    tokenizer_revision: str = Field(..., min_length=1, description="Pinned tokenizer commit.")
    image: str = Field(..., min_length=1, description="Container image and tag.")
    image_digest: str = Field(..., pattern=r"^sha256:[0-9a-f]{64}$", description="Image digest.")
    vllm_version: str = Field(..., min_length=1, description="Pinned vLLM release.")
    gpu_name: str = Field(..., min_length=1, description="Observed GPU model.")
    driver_version: str = Field(..., min_length=1, description="Observed NVIDIA driver.")
    host_key: str = Field(..., min_length=1, description="Configured cost host key.")
    provider: str = Field(..., min_length=1, description="Hosting provider.")
    sku: str = Field(..., min_length=1, description="Host SKU.")
    region: str = Field(..., min_length=1, description="Host region.")
    purchase_option: PurchaseOption = Field(..., description="Price option.")
    hourly_rate_usd: float = Field(..., gt=0, description="Bound hourly rate.")
    price_source_url: str = Field(..., min_length=1, description="Rate source.")
    price_verified_at: str = Field(..., min_length=1, description="Rate verification date.")
    weight_memory_gib: float = Field(..., gt=0, description="Parsed model allocation.")
    safetensors_total_gib: float = Field(..., gt=0, description="Repository weight-file total.")
    kv_cache_tokens: int = Field(..., gt=0, description="Parsed GPU KV-cache capacity.")
    maximum_concurrency: float = Field(..., gt=0, description="Parsed vLLM maximum concurrency.")


class LevelCheckpoint(BaseModel):
    """One complete arm/concurrency checkpoint; partial levels are discarded."""

    model_config = _MODEL_CONFIG

    arm: ArmName = Field(..., description="Benchmark arm.")
    concurrency: int = Field(..., gt=0, description="Closed-loop concurrency.")
    case_order_sha256: str = Field(..., pattern=_HASH, description="Ordered case-id hash.")
    cases_sha256: str = Field(..., pattern=_HASH, description="Case artifact hash.")
    started_at: datetime = Field(..., description="UTC level start.")
    completed_at: datetime = Field(..., description="UTC level completion.")
    warmup_completed: int = Field(..., ge=0, description="Excluded completed warm-ups.")
    measurements: tuple[RequestMeasurement, ...] = Field(
        ..., min_length=1, description="Every measured request in order."
    )
    quality_measurements: tuple[RequestMeasurement, ...] = Field(
        default=(), description="Excluded abstention-quality fixture observations."
    )
    telemetry: tuple[TelemetrySample, ...] = Field(default=(), description="Window GPU samples.")

    @model_validator(mode="after")
    def _complete_order(self) -> LevelCheckpoint:
        if [item.sequence for item in self.measurements] != list(range(len(self.measurements))):
            raise ValueError("measurements must cover a contiguous ordered level")
        if self.completed_at < self.started_at:
            raise ValueError("level completion cannot precede start")
        return self


class RunManifest(BaseModel):
    """Resumable benchmark state containing complete levels and per-arm provenance."""

    model_config = _MODEL_CONFIG

    run_id: str = Field(..., pattern=r"^vllm-bench-[0-9a-f]{16}$", description="Stable run id.")
    protocol_version: str = Field(..., min_length=1, description="Benchmark protocol.")
    profile: str = Field(..., min_length=1, description="Execution profile.")
    config_sha256: str = Field(..., pattern=_HASH, description="Config hash.")
    cases_sha256: str = Field(..., pattern=_HASH, description="Case artifact hash.")
    started_at: datetime = Field(..., description="UTC run start.")
    completed_at: datetime | None = Field(default=None, description="UTC full-matrix completion.")
    servers: dict[ArmName, ServerProvenance] = Field(
        default_factory=dict, description="Arm servers."
    )
    levels: dict[str, LevelCheckpoint] = Field(default_factory=dict, description="Complete levels.")


def case_artifact_sha256(artifact: CaseArtifact) -> str:
    """Return the canonical JSON SHA-256 for one in-memory case artifact."""
    return sha256_hex(canonical_json(artifact))


def write_case_artifact(path: Path, artifact: CaseArtifact) -> str:
    """Atomically persist one deterministic case artifact and return its byte hash."""
    atomic_write_model(path, artifact)
    return sha256_hex(path.read_bytes())


def load_case_artifact(path: Path) -> tuple[CaseArtifact, str]:
    """Strictly parse a case artifact and return its exact file hash."""
    raw = path.read_bytes()
    return CaseArtifact.model_validate_json(raw), sha256_hex(raw)


def _case_file_manifest(artifact: CaseArtifact, artifact_sha256: str) -> CaseFileManifest:
    """Build the exact preparation-time sidecar for one case artifact."""
    return CaseFileManifest(
        artifact_sha256=artifact_sha256,
        config_sha256=artifact.config_sha256,
        upstream_sha256=artifact.upstream_sha256,
        prompt_sha256=artifact.prompt_sha256,
        case_source=artifact.case_source,
        profile=artifact.profile,
        cases=len(artifact.cases),
        measured_cases=sum(case.case_set == "measured" for case in artifact.cases),
    )


def case_manifest_path(path: Path) -> Path:
    """Return the stable sidecar path for one case artifact."""
    return path.with_suffix(".manifest.json")


def write_case_bundle(path: Path, artifact: CaseArtifact) -> str:
    """Atomically install a canonical case artifact and its preparation-time SHA sidecar."""
    content = canonical_json(artifact)
    artifact_sha256 = sha256_hex(content)
    manifest = _case_file_manifest(artifact, artifact_sha256)
    install_bound_artifacts({path: content, case_manifest_path(path): canonical_json(manifest)})
    return artifact_sha256


def load_case_bundle(path: Path) -> tuple[CaseArtifact, str]:
    """Parse a case artifact and fail if its preparation-time SHA or lineage drifted."""
    artifact, artifact_sha256 = load_case_artifact(path)
    manifest = CaseFileManifest.model_validate_json(case_manifest_path(path).read_bytes())
    if manifest != _case_file_manifest(artifact, artifact_sha256):
        raise ValueError("case artifact preparation-time SHA or lineage drifted")
    return artifact, artifact_sha256


def initialize_run(  # noqa: PLR0913 - immutable identity fields make resume fail closed.
    path: Path,
    *,
    run_id: str,
    config: VllmBenchConfig,
    profile: str,
    cases_sha256: str,
    started_at: datetime,
) -> RunManifest:
    """Create a new manifest or validate immutable identity fields on resume."""
    if path.is_file():
        existing = load_run(path)
        expected = (run_id, config.protocol_version, profile, config.config_sha256, cases_sha256)
        observed = (
            existing.run_id,
            existing.protocol_version,
            existing.profile,
            existing.config_sha256,
            existing.cases_sha256,
        )
        if observed != expected:
            raise ValueError("run manifest identity drifted; start a new run id")
        return existing
    manifest = RunManifest(
        run_id=run_id,
        protocol_version=config.protocol_version,
        profile=profile,
        config_sha256=config.config_sha256,
        cases_sha256=cases_sha256,
        started_at=started_at,
    )
    write_run(path, manifest)
    return manifest


def write_run(path: Path, manifest: RunManifest) -> None:
    """Atomically persist complete run checkpoint state."""
    atomic_write_model(path, manifest)


def load_run(path: Path) -> RunManifest:
    """Strictly parse one run checkpoint manifest."""
    return RunManifest.model_validate_json(path.read_bytes())


def validate_restart_memory(previous_gib: float, observed_gib: float) -> None:
    """Reject server restarts whose parsed weight allocation differs by more than one percent."""
    if previous_gib <= 0 or observed_gib <= 0:
        raise ValueError("weight memory must be positive")
    if not math.isclose(previous_gib, observed_gib, rel_tol=0.01, abs_tol=0.0):
        raise ValueError("server restart weight memory drift exceeds one percent")
