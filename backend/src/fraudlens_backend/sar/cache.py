"""Summary: The SAR draft replay cache (plan §7.6 "SAR/RAG/embedding caches", §16 Phase 7).
A successful, schema-valid draft is keyed by a deterministic fingerprint of (prompt template hash,
model reference, the full PHI-free `SarInput`), so an identical investigation replays the stored
`SarDraftResult` with NO new provider spend and NO new tokens (`cached=True`) — the cost-control
"replay, no spend" path. `SarDraftCache` is a small protocol so a process-memory cache (the v1
default here) can be swapped for a persistent/shared backend later without touching the drafter.

Key classes:
- SarDraftCache: the get/set protocol the live drafter caches completed drafts through.
- InMemorySarDraftCache: a process-local dict-backed cache (the v1 default).

Key functions:
- sar_cache_key: derive the deterministic cache fingerprint for a (model, prompt, input) triple.

Notes:
- The fingerprint hashes the canonical JSON of the `SarInput`, so any change to the rules, SHAP
  drivers, citations, or risk band produces a different key (no stale-input replay).
- Only successful drafts are cached by the drafter; failures are never stored, so a transient
  provider failure is retried on the next request rather than replayed.
"""

from __future__ import annotations

import hashlib
from typing import Protocol, runtime_checkable

from fraudlens_ml.sar import SarDraftResult, SarInput


def sar_cache_key(model_id: str, prompt_hash: str, sar_input: SarInput) -> str:
    """Derive the deterministic cache fingerprint for a (model, prompt, input) triple."""
    canonical = "\n".join((model_id, prompt_hash, sar_input.model_dump_json()))
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
