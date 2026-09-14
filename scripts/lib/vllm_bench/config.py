"""Summary: Frozen, validated protocol configuration for the vLLM benchmark harness.

Key classes:
- CaseConfig: corpus source, quotas, strata, and full-data lineage.
- ArmConfig: one pinned BF16 or AWQ model/tokenizer arm.
- LogSourceConfig: provider-neutral startup-log retrieval configuration.
- ServerConfig: comparable vLLM server settings and log source.
- RequestConfig: exact streamed request controls.
- LoadConfig: concurrency, order, retry, and warm-up controls.
- TelemetryConfig: hosting-neutral GPU sampler configuration.
- HostCost: one provider/SKU price observation.
- TokenComparison: optional hosted-model token price comparison.
- CostConfig: host catalogue and optional token-cost comparison.
- QualityConfig: deterministic SAR-quality thresholds.
- AcceptanceConfig: publication acceptance thresholds.
- BenchmarkProfile: bounded case/concurrency overrides.
- BenchmarkPaths: constrained local artifact paths.
- VllmBenchConfig: complete immutable benchmark protocol.

Key functions:
- load_config: parse YAML and bind its exact byte SHA-256.
- resolve_profile: apply one named workload profile without changing the frozen protocol.

Notes:
- Secrets are represented only by environment-variable names and never parsed from YAML.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from ipaddress import ip_address
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_VLLM_BENCH_CONFIG = REPO_ROOT / "config" / "vllm-bench.yaml"
_HASH = r"^[0-9a-f]{64}$"
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

ArmName = Literal["bf16", "awq"]
CaseSource = Literal["ibm-final-test", "sar-eval"]
PurchaseOption = Literal["spot", "pay_as_you_go"]


class CaseConfig(BaseModel):
    """Case-corpus source, exact quotas, strata, and upstream configuration links."""

    model_config = _MODEL_CONFIG

    source: CaseSource = Field(..., description="Default benchmark case source.")
    count: int = Field(..., gt=0, description="Measured IBM case count.")
    quotas: dict[str, int] = Field(..., min_length=1, description="Per-candidate measured quotas.")
    dev_count: int = Field(..., gt=0, description="Separate development case count.")
    warmup_count: int = Field(..., gt=0, description="Separate warm-up case count.")
    abstention_fixtures: int = Field(..., gt=0, description="Evidence-removed fixture count.")
    strata: tuple[str, ...] = Field(..., min_length=1, description="Balanced case dimensions.")
    fulldata_config: str = Field(..., min_length=1, description="Full-data protocol path.")
    application_candidate: str = Field(..., min_length=1, description="Pre-registered scorer.")
    fixture_source: str = Field(..., min_length=1, description="SAR evaluation protocol path.")
    license: str = Field(..., min_length=1, description="Dataset derivative license identifier.")
    attribution: str = Field(..., min_length=1, description="Required dataset attribution.")
    source_url: str = Field(..., min_length=1, description="Authoritative dataset source URL.")

    @model_validator(mode="after")
    def _quota_total(self) -> CaseConfig:
        if sum(self.quotas.values()) != self.count or any(
            value <= 0 for value in self.quotas.values()
        ):
            raise ValueError("cases.quotas must be positive and sum to cases.count")
        if len(set(self.strata)) != len(self.strata):
            raise ValueError("cases.strata must not contain duplicates")
        return self


class ArmConfig(BaseModel):
    """One pinned model and tokenizer arm."""

    model_config = _MODEL_CONFIG

    model: str = Field(..., min_length=1, description="Hugging Face model repository.")
    revision: str = Field(..., pattern=r"^[0-9a-f]{40}$", description="Model commit SHA.")
    tokenizer: str = Field(..., min_length=1, description="Pinned tokenizer repository.")
    tokenizer_revision: str = Field(..., pattern=r"^[0-9a-f]{40}$", description="Tokenizer SHA.")
    dtype: Literal["bfloat16", "auto"] = Field(..., description="vLLM dtype argument.")
    quantization: Literal["awq_marlin"] | None = Field(
        default=None, description="Quantization implementation for the AWQ arm."
    )
    safetensors_total_gib: Decimal = Field(
        ..., gt=0, description="Repository weight bytes expressed in GiB."
    )


class LogSourceConfig(BaseModel):
    """How startup logs are collected without coupling to one host."""

    model_config = _MODEL_CONFIG

    kind: Literal["docker", "kubectl", "file"] = Field(..., description="Log source kind.")
    target: str = Field(..., min_length=1, description="Container, pod, or file target.")
    command_prefix: tuple[str, ...] = Field(
        default=(), description="Optional remote command prefix."
    )


class ServerConfig(BaseModel):
    """Comparable vLLM server controls shared by both arms."""

    model_config = _MODEL_CONFIG

    image: str = Field(..., min_length=1, description="Container repository without tag.")
    image_tag: str = Field(..., min_length=1, description="Pinned immutable release tag.")
    container_name: str = Field(..., min_length=1, description="Local container identity.")
    port: int = Field(..., ge=1, le=65535, description="OpenAI-compatible server port.")
    base_url: str = Field(..., min_length=1, description="Default OpenAI-compatible API origin.")
    base_url_env: str = Field(..., min_length=1, description="Base URL environment override name.")
    api_key_env: str = Field(..., min_length=1, description="API-key environment variable name.")
    image_digest_env: str = Field(..., min_length=1, description="Resolved image digest env name.")
    max_model_len: int = Field(..., gt=0, description="Identical context limit for both arms.")
    gpu_memory_utilization: Decimal = Field(..., gt=0, le=1, description="GPU memory fraction.")
    max_num_seqs: int = Field(..., gt=0, description="Maximum in-flight sequences.")
    enable_prefix_caching: Literal[False] = Field(..., description="Prefix caching stays disabled.")
    extra_args: tuple[str, ...] = Field(default=(), description="Frozen extra server arguments.")
    log_source: LogSourceConfig = Field(..., description="Startup log collection source.")


class RequestConfig(BaseModel):
    """Exact streaming request and retry controls."""

    model_config = _MODEL_CONFIG

    max_tokens: int = Field(..., gt=0, description="Maximum generated tokens per request.")
    temperature: float = Field(
        ..., ge=0.0, le=0.0, description="Deterministic sampling temperature."
    )
    json_object_mode: Literal[True] = Field(..., description="Request JSON-object mode.")
    seed_requests: Literal[True] = Field(..., description="Seed every request deterministically.")
    timeout_s: float = Field(..., gt=0, description="Per-attempt HTTP timeout.")
    max_attempts: int = Field(..., ge=1, description="Harness-owned total attempts.")
    allowed_plain_http_hosts: tuple[str, ...] = Field(
        ..., min_length=1, description="Explicit development HTTP hosts."
    )

    @field_validator("allowed_plain_http_hosts")
    @classmethod
    def _safe_http_hosts(cls, hosts: tuple[str, ...]) -> tuple[str, ...]:
        if len(hosts) != len(set(hosts)):
            raise ValueError("request.allowed_plain_http_hosts must be unique")
        for host in hosts:
            if host == "localhost":
                continue
            try:
                if not (ip_address(host).is_loopback or ip_address(host).is_private):
                    raise ValueError
            except ValueError as exc:
                raise ValueError("plain HTTP hosts must be loopback or private IPs") from exc
        return hosts


class LoadConfig(BaseModel):
    """Closed-loop load shape and fairness controls."""

    model_config = _MODEL_CONFIG

    concurrency_levels: tuple[int, ...] = Field(..., min_length=1, description="Load levels.")
    warmup_requests: int = Field(..., ge=0, description="Excluded warm-up request count.")
    cooldown_s: float = Field(..., ge=0, description="Cooldown between levels.")
    case_order: Literal["shuffled"] = Field(..., description="Seeded case ordering policy.")
    max_error_rate: float = Field(..., ge=0, le=1, description="Maximum accepted error rate.")

    @field_validator("concurrency_levels")
    @classmethod
    def _ordered_levels(cls, levels: tuple[int, ...]) -> tuple[int, ...]:
        if any(value <= 0 for value in levels) or tuple(sorted(set(levels))) != levels:
            raise ValueError("load.concurrency_levels must be positive, unique, and increasing")
        return levels


class TelemetryConfig(BaseModel):
    """Hosting-neutral GPU and vLLM telemetry sampling controls."""

    model_config = _MODEL_CONFIG

    sampler: Literal["nvidia_smi", "dcgm", "none"] = Field(..., description="GPU sampler.")
    interval_s: float = Field(..., gt=0, description="Sampling interval.")
    command_prefix: tuple[str, ...] = Field(default=(), description="Optional SSH/exec prefix.")
    dcgm_url: str | None = Field(default=None, description="Optional DCGM exporter URL.")
    prometheus_url: str | None = Field(default=None, description="Optional vLLM metrics URL.")


class HostCost(BaseModel):
    """One provider/SKU hourly price observation."""

    model_config = _MODEL_CONFIG

    provider: str = Field(..., min_length=1, description="Hosting provider.")
    sku: str = Field(..., min_length=1, description="Host or GPU SKU.")
    region: str = Field(..., min_length=1, description="Region or market.")
    prices: dict[PurchaseOption, Decimal] = Field(
        ..., min_length=1, description="Hourly USD rates."
    )
    price_source_url: str = Field(..., min_length=1, description="Rate provenance URL.")
    price_verified_at: date = Field(..., description="Date the configured rate was verified.")

    @field_validator("prices")
    @classmethod
    def _positive_prices(
        cls, prices: dict[PurchaseOption, Decimal]
    ) -> dict[PurchaseOption, Decimal]:
        if any(value <= 0 for value in prices.values()):
            raise ValueError("host prices must be positive")
        return prices


class TokenComparison(BaseModel):
    """Optional hosted-model token price comparison."""

    model_config = _MODEL_CONFIG

    model: str = Field(..., min_length=1, description="Catalog comparison model reference.")
    input_per_million_usd: Decimal = Field(..., ge=0, description="Input-token price.")
    output_per_million_usd: Decimal = Field(..., ge=0, description="Output-token price.")


class CostConfig(BaseModel):
    """Cloud-neutral host catalogue and reporting unit."""

    model_config = _MODEL_CONFIG

    default_host: str = Field(..., min_length=1, description="Default host key.")
    hosts: dict[str, HostCost] = Field(..., min_length=1, description="Configured host catalogue.")
    comparison_model: TokenComparison | None = Field(default=None, description="Token baseline.")
    drafts_per_unit: int = Field(..., gt=0, description="Normalized cost draft count.")

    @model_validator(mode="after")
    def _default_exists(self) -> CostConfig:
        if self.default_host not in self.hosts:
            raise ValueError("cost.default_host must exist in cost.hosts")
        return self


class QualityConfig(BaseModel):
    """Deterministic output-quality gates and warnings."""

    model_config = _MODEL_CONFIG

    schema_valid_min: float = Field(..., ge=0, le=1, description="Minimum schema-valid rate.")
    reference_validity_min: float = Field(..., ge=0, le=1, description="Citation validity floor.")
    coverage_warn_min: float = Field(..., ge=0, le=1, description="Required-fact warning floor.")
    awq_delta_warn_pp: float = Field(..., ge=0, description="Quality delta warning points.")


class AcceptanceConfig(BaseModel):
    """Publication acceptance thresholds."""

    model_config = _MODEL_CONFIG

    weight_memory_reduction_min: float = Field(..., ge=0, le=1, description="AWQ reduction floor.")
    token_accounting_drift_max: float = Field(..., ge=0, le=1, description="Usage drift cap.")
    require_gpu_telemetry: bool = Field(..., description="Whether telemetry is mandatory.")


class BenchmarkProfile(BaseModel):
    """A bounded workload override for smoke or full execution."""

    model_config = _MODEL_CONFIG

    cases_count: int | None = Field(default=None, gt=0, description="Measured case cap override.")
    concurrency_levels: tuple[int, ...] | None = Field(default=None, description="Level override.")
    warmup_requests: int | None = Field(default=None, ge=0, description="Warm-up override.")


class BenchmarkPaths(BaseModel):
    """Gitignored benchmark scratch directory."""

    model_config = _MODEL_CONFIG

    output_dir: str = Field(..., description="Repository-relative scratch directory.")

    @field_validator("output_dir")
    @classmethod
    def _local_only(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or path.parts[:1] != (".local",):
            raise ValueError("paths.output_dir must be repository-relative below .local/")
        return value


class VllmBenchConfig(BaseModel):
    """Complete frozen BF16-versus-AWQ benchmark protocol."""

    model_config = _MODEL_CONFIG

    seed: int = Field(..., ge=0, description="Case order and request seed.")
    protocol_version: str = Field(..., min_length=1, description="Immutable protocol version.")
    cases: CaseConfig = Field(..., description="Case corpus contract.")
    arms: dict[ArmName, ArmConfig] = Field(..., description="Exactly BF16 and AWQ arms.")
    server: ServerConfig = Field(..., description="Comparable server configuration.")
    request: RequestConfig = Field(..., description="Streamed request controls.")
    load: LoadConfig = Field(..., description="Closed-loop load controls.")
    telemetry: TelemetryConfig = Field(..., description="GPU telemetry controls.")
    cost: CostConfig = Field(..., description="Cloud-neutral cost model.")
    quality: QualityConfig = Field(..., description="Output quality gates.")
    acceptance: AcceptanceConfig = Field(..., description="Publication criteria.")
    kv_cache_mode: Literal["equal_utilization", "equal_kv_gib"] = Field(
        ..., description="Primary or optional KV-cache comparison mode."
    )
    paths: BenchmarkPaths = Field(..., description="Scratch locations.")
    profiles: dict[str, BenchmarkProfile] = Field(
        ..., min_length=1, description="Workload profiles."
    )
    config_sha256: str = Field(default="", exclude=True, pattern=_HASH, description="Config hash.")

    @model_validator(mode="after")
    def _fairness_contract(self) -> VllmBenchConfig:
        if set(self.arms) != {"bf16", "awq"}:
            raise ValueError("arms must contain exactly bf16 and awq")
        if self.arms["bf16"].quantization is not None or self.arms["bf16"].dtype != "bfloat16":
            raise ValueError("bf16 arm must use bfloat16 without quantization")
        if self.arms["awq"].quantization != "awq_marlin":
            raise ValueError("awq arm must use awq_marlin")
        if (
            self.arms["bf16"].tokenizer != self.arms["awq"].tokenizer
            or self.arms["bf16"].tokenizer_revision != self.arms["awq"].tokenizer_revision
        ):
            raise ValueError("both arms must use the identical pinned tokenizer")
        if "smoke" not in self.profiles or "full" not in self.profiles:
            raise ValueError("profiles must contain smoke and full")
        if self.profiles["full"].model_dump(exclude_none=True):
            raise ValueError("profiles.full must not override the frozen protocol")
        return self


def load_config(path: Path = DEFAULT_VLLM_BENCH_CONFIG) -> VllmBenchConfig:
    """Parse the benchmark YAML and bind its exact byte hash."""
    raw = path.read_bytes()
    payload: Any = yaml.safe_load(raw)
    config = VllmBenchConfig.model_validate(payload)
    return config.model_copy(update={"config_sha256": hashlib.sha256(raw).hexdigest()})


def resolve_profile(config: VllmBenchConfig, profile: str) -> tuple[int, tuple[int, ...], int]:
    """Return effective measured cases, concurrency levels, and warm-up requests."""
    selected = config.profiles.get(profile)
    if selected is None:
        raise ValueError(f"unknown benchmark profile '{profile}'")
    return (
        selected.cases_count or config.cases.count,
        selected.concurrency_levels or config.load.concurrency_levels,
        selected.warmup_requests
        if selected.warmup_requests is not None
        else config.load.warmup_requests,
    )
