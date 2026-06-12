"""Summary: Runtime settings for the standalone LLM client. Settings discover the
repo config files by default, read `FRAUDLENS_LLM_*` overrides, fall back to
`FRAUDLENS_ENVIRONMENT` for environment, and reject unsafe guardrail settings in
production.

Key classes:
- LlmSettings: Pydantic-settings model for LLM client runtime knobs.

Key functions:
- get_llm_settings: Cached settings factory.

Notes:
- Secrets are not modeled here; providers resolve API key env-var references lazily.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from fraudlens_llm.catalog import GenerationParams
from fraudlens_llm.models import DataClass, PhiMaskingMode, Strictness


def _repo_root() -> Path:
    """Return the repository root by walking up to config/llm."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "config" / "llm").is_dir():
            return parent
    return Path.cwd()


def _catalog_path() -> Path:
    """Return the default catalog path."""
    return _repo_root() / "config" / "llm" / "catalog.yml"


def _providers_path() -> Path:
    """Return the default providers path."""
    return _repo_root() / "config" / "llm" / "providers.yml"


def _environment_default() -> Literal["dev", "prod"]:
    """Return the non-LLM environment fallback."""
    return "prod" if os.getenv("FRAUDLENS_ENVIRONMENT") == "prod" else "dev"


class LlmSettings(BaseSettings):
    """Runtime settings for the FraudLens LLM client."""

    model_config = SettingsConfigDict(
        env_prefix="FRAUDLENS_LLM_",
        extra="ignore",
        frozen=True,
        use_enum_values=False,
    )

    catalog_path: Path = Field(
        default_factory=_catalog_path, description="Path to config/llm/catalog.yml."
    )
    providers_path: Path = Field(
        default_factory=_providers_path, description="Path to config/llm/providers.yml."
    )
    default_model: str = Field(
        # Overridable catalog fallback (FRAUDLENS_LLM_DEFAULT_MODEL / config); real model
        # selection is config-driven, so this is a safe default, not a hardcoded endpoint.
        default="openai/gpt-5-mini",  # allow-hardcoded
        description="Default provider/model reference for chat.",
    )
    default_data_class: DataClass = Field(
        default=DataClass.SYNTHETIC, description="Default data class for calls."
    )
    default_params: GenerationParams = Field(
        default_factory=lambda: GenerationParams(temperature=0.05, max_tokens=1024),
        description="Default generation parameters when the catalog omits them.",
    )
    guardrail_strictness: Strictness = Field(
        default=Strictness.BLOCK, description="Guardrail enforcement strictness."
    )
    phi_masking_mode: PhiMaskingMode = Field(
        default=PhiMaskingMode.ENFORCE, description="PHI masking mode."
    )
    allow_raw_output: bool = Field(
        default=False, description="Whether raw provider text may be returned outside prod."
    )
    allow_policy_downgrade: bool = Field(
        default=False, description="Allow fallback to weaker governance posture outside prod."
    )
    environment: Literal["dev", "prod"] = Field(
        default_factory=_environment_default,
        description="LLM runtime environment; FRAUDLENS_LLM_ENVIRONMENT overrides.",
    )

    @model_validator(mode="after")
    def _prod_guardrails_fail_closed(self) -> LlmSettings:
        """Reject unsafe guardrail settings in production."""
        if self.environment != "prod":
            return self
        if self.guardrail_strictness == Strictness.DISABLED:
            raise ValueError("prod forbids disabled LLM guardrails")
        if self.phi_masking_mode == PhiMaskingMode.OFF:
            raise ValueError("prod forbids disabling PHI masking")
        if self.allow_raw_output:
            raise ValueError("prod forbids raw LLM output")
        if self.allow_policy_downgrade:
            raise ValueError("prod forbids provider policy downgrade")
        return self


@lru_cache
def get_llm_settings() -> LlmSettings:
    """Return cached LLM settings."""
    return LlmSettings()
