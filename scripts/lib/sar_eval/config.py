"""Summary: Frozen protocol configuration for the paired multi-agent SAR evaluation.
The loader validates the exact eight-typology by four-variant matrix, judge model,
sampling and bootstrap pins, API limits, and scratch-only paths before any spending stage.

Key classes:
- SarTypology: the eight synthetic AML patterns in the study.
- ScenarioVariant: the four evidence-quality variants applied to every typology.
- JudgeConfig: pinned structured-judge sampling and token bounds.
- ApiConfig: API timeout, polling, and per-run reservation limits.
- BootstrapConfig: fixed paired BCa resampling protocol.
- SarEvalPaths: repository-relative scratch and corpus paths.
- SarEvalConfig: the complete committed, non-secret protocol.

Key functions:
- load_sar_eval_config: parse and validate config/sar-eval.yaml.
- validate_config_binding: require an artifact SHA to match the loaded protocol bytes.

Notes:
- Secrets and bearer tokens are environment-only and never fields on this model.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from ipaddress import ip_address
from pathlib import Path, PurePosixPath

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SAR_EVAL_CONFIG = _REPO_ROOT / "config" / "sar-eval.yaml"
_REQUIRED_JUDGE_SAMPLES = 3
_REQUIRED_BOOTSTRAP_RESAMPLES = 10_000
_REQUIRED_CONFIDENCE_LEVEL = 0.95
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class SarTypology(StrEnum):
    """The eight synthetic AML patterns exercised by the evaluation."""

    STRUCTURING = "structuring"
    HIGH_RISK_WIRE = "high_risk_wire"
    RAPID_MOVEMENT = "rapid_movement"
    FUNNEL_ACCOUNT = "funnel_account"
    MULE_VELOCITY = "mule_velocity"
    ROUND_AMOUNT_LAYERING = "round_amount_layering"
    CRYPTO_OFF_RAMP = "crypto_off_ramp"
    SHELL_COMPANY_TRANSFER = "shell_company_transfer"


class ScenarioVariant(StrEnum):
    """The canonical evidence variants applied to every typology."""

    CLEAN = "clean"
    THIN_EVIDENCE = "thin_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    CITATION_BAIT = "citation_bait"


class JudgeConfig(BaseModel):
    """Model and bounded generation settings for the blind judge."""

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    model: str = Field(..., min_length=1, description="Catalog model reference for the judge.")
    prompt_id: str = Field(..., min_length=1, description="Versioned judge prompt file id.")
    samples_per_narrative: int = Field(
        ..., description="Independent judge samples per narrative; protocol requires three."
    )
    max_input_bytes: int = Field(
        ...,
        gt=0,
        description=(
            "Maximum UTF-8 input bytes per call; also used as a conservative token upper bound."
        ),
    )
    max_output_tokens: int = Field(..., gt=0, description="Maximum judge output tokens per call.")
    temperature: float = Field(..., ge=0.0, le=1.0, description="Judge sampling temperature.")

    @model_validator(mode="after")
    def _three_samples(self) -> JudgeConfig:
        if self.samples_per_narrative != _REQUIRED_JUDGE_SAMPLES:
            raise ValueError("judge.samples_per_narrative must be exactly 3")
        return self


class ApiConfig(BaseModel):
    """Polling and conservative spend bounds for the shipped API harness."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    timeout_s: float = Field(..., gt=0, description="HTTP request timeout in seconds.")
    poll_interval_s: float = Field(..., gt=0, description="Snapshot poll interval in seconds.")
    run_timeout_s: float = Field(..., gt=0, description="Maximum wait for one investigation.")
    max_cost_usd_per_run: float = Field(
        ..., gt=0, description="Conservative reservation and observed cap for each arm run."
    )
    loopback_http_hosts: tuple[str, ...] = Field(
        ...,
        min_length=1,
        description="Explicit loopback hosts allowed to receive bearer auth over local HTTP.",
    )

    @field_validator("loopback_http_hosts")
    @classmethod
    def _loopback_only(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("api.loopback_http_hosts must not contain duplicates")
        for host in value:
            if host == "localhost":
                continue
            try:
                is_loopback = ip_address(host).is_loopback
            except ValueError as exc:
                raise ValueError(
                    "api.loopback_http_hosts must contain only loopback hosts"
                ) from exc
            if not is_loopback:
                raise ValueError("api.loopback_http_hosts must contain only loopback hosts")
        return value


class BootstrapConfig(BaseModel):
    """Fixed paired BCa bootstrap protocol."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    resamples: int = Field(..., description="Paired bootstrap resamples; protocol requires 10,000.")
    confidence_level: float = Field(..., description="Two-sided confidence level.")

    @model_validator(mode="after")
    def _pins(self) -> BootstrapConfig:
        if self.resamples != _REQUIRED_BOOTSTRAP_RESAMPLES:
            raise ValueError("bootstrap.resamples must be exactly 10000")
        if self.confidence_level != _REQUIRED_CONFIDENCE_LEVEL:
            raise ValueError("bootstrap.confidence_level must be exactly 0.95")
        return self


class SarEvalPaths(BaseModel):
    """Scratch paths for resumable, never-committed stage artifacts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    output_dir: str = Field(..., description="Per-run output root under .local/.")
    corpus_dir: str = Field(..., description="Committed public regulatory corpus directory.")

    @field_validator("output_dir")
    @classmethod
    def _scratch_only(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or path.parts[:1] != (".local",):
            raise ValueError("paths.output_dir must be repo-relative under .local/")
        return value

    @field_validator("corpus_dir")
    @classmethod
    def _repo_relative(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("paths.corpus_dir must be repo-relative without traversal")
        return value


class SarEvalConfig(BaseModel):
    """The full frozen SAR evaluation protocol."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    seed: int = Field(..., ge=0, description="Deterministic scenario/order/bootstrap seed.")
    anchor_time: datetime = Field(..., description="UTC scenario time anchor.")
    typologies: tuple[SarTypology, ...] = Field(..., description="Exact eight typologies.")
    variants: tuple[ScenarioVariant, ...] = Field(..., description="Exact four variants.")
    judge: JudgeConfig = Field(..., description="Blind judge protocol.")
    api: ApiConfig = Field(..., description="API runner bounds.")
    bootstrap: BootstrapConfig = Field(..., description="Paired BCa protocol.")
    paths: SarEvalPaths = Field(..., description="Scratch and corpus paths.")
    config_sha256: str | None = Field(
        default=None,
        exclude=True,
        pattern=_SHA256_PATTERN,
        description="Runtime SHA-256 of the exact loaded protocol bytes.",
    )

    @model_validator(mode="after")
    def _matrix_exact(self) -> SarEvalConfig:
        if self.typologies != tuple(SarTypology):
            raise ValueError(
                "typologies must list every SarTypology exactly once in canonical order"
            )
        if self.variants != tuple(ScenarioVariant):
            raise ValueError(
                "variants must list every ScenarioVariant exactly once in canonical order"
            )
        return self


def load_sar_eval_config(path: Path | None = None) -> SarEvalConfig:
    """Load and validate the committed non-secret evaluation protocol."""
    target = path or DEFAULT_SAR_EVAL_CONFIG
    raw_bytes = target.read_bytes()
    raw = yaml.safe_load(raw_bytes)
    if not isinstance(raw, dict):
        raise ValueError(f"{target} must contain a YAML mapping")
    parsed = SarEvalConfig.model_validate(raw)
    return parsed.model_copy(update={"config_sha256": hashlib.sha256(raw_bytes).hexdigest()})


def validate_config_binding(config: SarEvalConfig, observed_sha256: str) -> None:
    """Require an artifact to bind to the exact bytes that produced the loaded config."""
    if config.config_sha256 is None:
        raise ValueError("loaded evaluation config has no source SHA-256 binding")
    if observed_sha256 != config.config_sha256:
        raise ValueError("evaluation artifact config SHA-256 does not match the loaded protocol")
