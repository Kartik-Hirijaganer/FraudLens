"""Summary: The SAR draft replay cache (plan §7.6 "SAR/RAG/embedding caches", §16 Phase 7).
A successful, GATE-PASSING draft is keyed by a deterministic fingerprint of tenant, prompt hash +
version, model reference, connection route, quality-policy hash, exact egress evidence, and
generation settings, so an identical investigation replays the stored
`SarDraftResult` with NO new provider spend and NO new tokens (`cached=True`) — the cost-control
"replay, no spend" path. `SarDraftCache` is a small protocol so a process-memory cache (the v1
default here) can be swapped for a persistent/shared backend later without touching the drafter.

Key classes:
- SarCacheGenerationSettings:
- SarDraftCache: the get/set protocol the live drafter caches completed drafts through.
- InMemorySarDraftCache: a process-local dict-backed cache (the v1 default).

Key functions:
- sar_cache_key: derive the deterministic tenant/evidence/prompt/model/settings fingerprint.

Notes:
- The fingerprint hashes the canonical JSON of the `SarInput`, so any change to the rules, SHAP
drivers, citations, or risk band produces a different key (no stale-input replay).
- Since release 0.5.0 it ALSO binds the cascade stage, connection route, constrained-decoding mode,
prompt version, and the quality-policy hash: a cached pre-gate draft replaying past a tightened
policy or a changed prompt would bypass the gate entirely, which is the one way a replay could
serve an artifact the current rules would reject.
- Only successful drafts are cached by the drafter; failures are never stored, so a transient
provider failure is retried on the next request rather than replayed.
"""

from __future__ import annotations

import hashlib
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from fraudlens_backend.sar.egress import SarModelInput
from fraudlens_ml.sar import SarDraftResult


class SarCacheGenerationSettings(BaseModel):
    """Generation settings whose change must invalidate a draft replay."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_output_tokens: int = Field(..., gt=0, description="Maximum generated tokens.")
    reasoning_effort: str | None = Field(default=None, description="Reasoning effort hint.")
    fallbacks: tuple[str, ...] = Field(default=(), description="Ordered governed fallback refs.")
    task_type: str = Field(..., min_length=1, description="Guardrail task classification.")
    profile_stage: str = Field(
        default="", description="Cascade profile stage that produced the draft."
    )
    connection: str | None = Field(default=None, description="Named connection route used.")
    constrained_decoding: bool = Field(
        default=False, description="Whether the stage requested a closed structured-output schema."
    )
    prompt_version: str = Field(default="", description="Versioned prompt id that was rendered.")
    quality_policy_hash: str = Field(
        default="", description="Hash of the runtime quality policy the draft was accepted under."
    )


def sar_cache_key(
    *,
    model_id: str,
    prompt_hash: str,
    agency_id: str,
    model_input: SarModelInput,
    generation_settings: SarCacheGenerationSettings,
) -> str:
    """Hash the exact tenant, evidence, prompt, model, and generation configuration."""
    canonical = "\n".join(
        (
            agency_id,
            model_id,
            prompt_hash,
            generation_settings.model_dump_json(),
            model_input.model_dump_json(by_alias=True),
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@runtime_checkable
class SarDraftCache(Protocol):
    """The get/set protocol used to replay completed SAR drafts without re-spending."""

    def get(self, key: str) -> SarDraftResult | None:
        """Return the cached result for a key, or None on a miss."""
        ...

    def set(self, key: str, result: SarDraftResult) -> None:
        """Store a completed draft result under a key."""
        ...


class InMemorySarDraftCache:
    """A process-local dict-backed SAR draft cache (the v1 default)."""

    def __init__(self) -> None:
        """Initialize the empty in-process store."""
        self._store: dict[str, SarDraftResult] = {}

    def get(self, key: str) -> SarDraftResult | None:
        """Return the cached result for a key, or None on a miss."""
        return self._store.get(key)

    def set(self, key: str, result: SarDraftResult) -> None:
        """Store a completed draft result under a key."""
        self._store[key] = result
