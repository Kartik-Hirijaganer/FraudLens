"""Summary: The deterministic request order and per-arm server binding every scenario shares.

Key classes:
- (none)

Key functions:
- ordered_cases: derive the identical deterministic request order used by both arms.
- bind_server: validate restart identity/memory and attach first-seen per-arm provenance.

Notes:
- Level EXECUTION lives in `cascade_load`: a level is run by the production quality-gated
  drafter through a named SAR profile, never by a benchmark-only HTTP client. The v1
  model-only runner was retired in release 0.5.0 once the full run measured its raw arms as
  single-stage scenarios and the report's `gate_verdict_parity` criterion proved the recorded
  and re-derived gate verdicts identical across all 1,092 attempts.
- What stays here is what BOTH producers share: the deterministic request order every
  scenario must reuse, and the restart-identity check that binds per-arm provenance.
"""

from __future__ import annotations

import random

from lib.vllm_bench.config import VllmBenchConfig, resolve_case_set, resolve_profile
from lib.vllm_bench.state import (
    BenchmarkCase,
    CaseArtifact,
    RunManifest,
    ServerProvenance,
    validate_restart_memory,
)


def ordered_cases(
    artifact: CaseArtifact,
    config: VllmBenchConfig,
    *,
    profile: str,
    concurrency: int,
) -> tuple[BenchmarkCase, ...]:
    """Return the seeded profile-selected case order shared by both arms for one level."""
    requested, _levels, _warmups = resolve_profile(config, profile)
    case_set = resolve_case_set(config, profile)
    candidates = [case for case in artifact.cases if case.case_set == case_set]
    if len(candidates) < requested:
        raise ValueError(
            f"case artifact has {len(candidates)} {case_set} cases; {requested} required"
        )
    selected = candidates[:requested]
    random.Random(config.seed + concurrency).shuffle(selected)
    return tuple(selected)


def bind_server(manifest: RunManifest, provenance: ServerProvenance) -> RunManifest:
    """Validate restart identity/memory and attach first-seen per-arm provenance."""
    prior = manifest.servers.get(provenance.arm)
    if prior is not None:
        validate_restart_memory(prior.weight_memory_gib, provenance.weight_memory_gib)
        normalized = prior.model_copy(update={"weight_memory_gib": provenance.weight_memory_gib})
        if normalized != provenance:
            raise ValueError("server provenance drifted while resuming an arm")
        return manifest
    return manifest.model_copy(update={"servers": {**manifest.servers, provenance.arm: provenance}})
