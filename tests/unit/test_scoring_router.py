"""Phase 5 canary-router tests (plan §16 Phase 5; lifecycle wiring in Phase 10). Verify the
deterministic active-vs-canary split: inert with no canary / 0%, stable per routing key, and
roughly canary_percent% of keys route to the canary at intermediate percentages."""

from __future__ import annotations

from fraudlens_ml.scoring import CanaryDeployment, CanaryRouter, RoutingDecision

ROUTER = CanaryRouter()


def _deployment(percent: int, *, with_canary: bool = True) -> CanaryDeployment:
    return CanaryDeployment(
        active_version_label="active-v1",
        active_artifact_uri="active-v1",
        canary_version_label="canary-v2" if with_canary else None,
        canary_artifact_uri="canary-v2" if with_canary else None,
        canary_percent=percent,
    )


def test_no_canary_configured_routes_to_active() -> None:
    decision = ROUTER.route(_deployment(50, with_canary=False), "txn-1")
    assert decision == RoutingDecision(
        version_label="active-v1", artifact_uri="active-v1", was_canary=False
    )


def test_zero_percent_always_routes_to_active() -> None:
    for key in ("a", "b", "c", "d", "e"):
        assert ROUTER.route(_deployment(0), key).was_canary is False


def test_full_percent_always_routes_to_canary() -> None:
    for key in ("a", "b", "c", "d", "e"):
        decision = ROUTER.route(_deployment(100), key)
        assert decision.was_canary is True
        assert decision.version_label == "canary-v2"


def test_routing_is_deterministic_per_key() -> None:
    deployment = _deployment(50)
    first = ROUTER.route(deployment, "stable-key")
    again = ROUTER.route(deployment, "stable-key")
    assert first == again


def test_intermediate_percent_splits_traffic() -> None:
    deployment = _deployment(25)
    keys = [f"txn-{i}" for i in range(2000)]
    canary = sum(ROUTER.route(deployment, key).was_canary for key in keys)
    fraction = canary / len(keys)
    assert 0.20 < fraction < 0.30  # ~25% land on the canary
