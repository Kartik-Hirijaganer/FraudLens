"""Summary: Strict loader for the shared quality policy in config/quality.yaml. It composes the
existing physical-file policy with SAR citation, hallucination, and required-fact thresholds plus
the model-egress policy reference used by the named CI gate.

Key classes:
- SarQualityThresholds: deterministic acceptance thresholds plus the runtime gate policy.
- QualityConfig: complete repository quality policy.
- QualityConfigError: safe configuration-load failure.

Key functions:
- load_quality_config: load and strictly validate the committed quality YAML.

Notes:
- Thresholds are data, not source constants, so tightening the committed or test policy changes
  gate behavior without modifying the metric implementations.
- `runtime_gate` and `replay_gate` reuse the backend `SarRuntimeGatePolicy` type rather than
  mirroring its fields, so the offline gates, the replay pilot, and the production drafter
  validate one schema over one file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fraudlens_backend.sar.quality_gate import SarRuntimeGatePolicy
from lib.file_length import FileLengthSettings

DEFAULT_QUALITY_CONFIG = Path(__file__).resolve().parents[3] / "config" / "quality.yaml"


class SarQualityThresholds(BaseModel):
    """Thresholds enforced by the deterministic SAR quality test suites."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    citation_precision_min: float = Field(
        ..., ge=0, le=1, description="Minimum offered-citation precision."
    )
    citation_recall_min: float = Field(
        ..., ge=0, le=1, description="Minimum expected-citation recall."
    )
    unsupported_claim_recall_min: float = Field(
        ..., ge=0, le=1, description="Minimum recall on planted unsupported claims."
    )
    clean_draft_false_positive_max: float = Field(
        ..., ge=0, le=1, description="Maximum false-positive rate on supported clean claims."
    )
    required_fact_coverage_min: float = Field(
        ..., ge=0, le=1, description="Minimum required case-fact coverage in rendered drafts."
    )
    runtime_gate: SarRuntimeGatePolicy = Field(
        ..., description="Production SARQualityGate rule switches (same file, one source)."
    )
    replay_gate: SarRuntimeGatePolicy = Field(
        ..., description="Gate policy the persisted prompt-v1 benchmark output is replayed under."
    )


class QualityConfig(BaseModel):
    """Complete deterministic quality and privacy-gate configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    file_length: FileLengthSettings = Field(..., description="Physical source-file size policy.")
    sar_quality: SarQualityThresholds = Field(..., description="SAR quality thresholds.")
    egress_policy_file: str = Field(
        ..., min_length=1, description="Repository-relative model-egress policy YAML."
    )


class QualityConfigError(RuntimeError):
    """Raised when the quality policy cannot be loaded or validated."""


def load_quality_config(path: Path = DEFAULT_QUALITY_CONFIG) -> QualityConfig:
    """Load the committed quality policy with duplicate/unknown fields rejected by Pydantic."""
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        return QualityConfig.model_validate(raw or {})
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise QualityConfigError(f"Quality configuration is invalid: {path}") from exc
