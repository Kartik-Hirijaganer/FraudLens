"""Summary: The canary routing stub (plan §16 Phase 5; full lifecycle in Phase 10). During a
canary rollout the active pointer also names a candidate `canary` version at some percent
(5 -> 25 -> 50 -> 100); `CanaryRouter.route` deterministically decides, per transaction,
whether this run scores with the canary or the active model. The split is a stable hash of a
caller-supplied routing key (e.g. the transaction id), so the same transaction always lands in
the same bucket — re-runs and replays route identically, and roughly `canary_percent`% of
distinct keys hit the canary. Phase 5 ships only this deterministic decision; Phase 10 wires it
into the pipeline, writes `model_inference_logs` for both models, and adds the auto-abort/
rollback on a canary metric deviation (plan §10.5 / §10.5.1). Pure + dependency-free so the
routing rule is trivially unit-testable.

Key classes:
- CanaryDeployment: the active (+ optional canary at a percent) versions in play.
- RoutingDecision: which version a transaction routed to (label, uri, was-canary).
- CanaryRouter: deterministic per-transaction active-vs-canary routing.

Key functions:
- (none)

Notes:
- With no canary configured, or canary_percent <= 0, every transaction routes to active, so
  the stub is inert until a rollout sets a canary — matching the seeded default (percent 0).
- The bucket is a SHA-256 of the routing key mod 100, so routing is deterministic and stable
  across processes (no RNG), and a given key's bucket never drifts between runs.
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict, Field

_BUCKETS = 100


class CanaryDeployment(BaseModel):
    """The active model and an optional canary candidate at a rollout percent (plan §10.5)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    active_version_label: str = Field(..., description="The active model's version label.")
    active_artifact_uri: str = Field(..., description="The active model's artifact uri.")
    canary_version_label: str | None = Field(
        default=None, description="The canary candidate's version label (None when no rollout)."
    )
    canary_artifact_uri: str | None = Field(
        default=None, description="The canary candidate's artifact uri (None when no rollout)."
    )
    canary_percent: int = Field(
        default=0, ge=0, le=100, description="Percent of traffic routed to the canary (0 = off)."
    )


class RoutingDecision(BaseModel):
    """Which model version a transaction routed to, and whether that was the canary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version_label: str = Field(..., description="The routed model version label.")
    artifact_uri: str = Field(..., description="The routed model artifact uri.")
    was_canary: bool = Field(..., description="True when the transaction routed to the canary.")


class CanaryRouter:
    """Deterministically routes each transaction to the active or canary model by hash bucket."""

    @staticmethod
    def _bucket(routing_key: str) -> int:
        """Return a stable [0, 100) bucket for a routing key (no RNG; replay-stable)."""
        digest = hashlib.sha256(routing_key.encode("utf-8")).hexdigest()
        return int(digest, 16) % _BUCKETS

    def route(self, deployment: CanaryDeployment, routing_key: str) -> RoutingDecision:
        """Pick the canary for ~canary_percent% of keys, else the active model."""
        if (
            deployment.canary_version_label is not None
            and deployment.canary_artifact_uri is not None
            and deployment.canary_percent > 0
            and self._bucket(routing_key) < deployment.canary_percent
        ):
            return RoutingDecision(
                version_label=deployment.canary_version_label,
                artifact_uri=deployment.canary_artifact_uri,
                was_canary=True,
            )
        return RoutingDecision(
            version_label=deployment.active_version_label,
            artifact_uri=deployment.active_artifact_uri,
            was_canary=False,
        )
