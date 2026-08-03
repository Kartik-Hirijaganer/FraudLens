"""Portfolio-demo API smoke tests (plan §16 Phase 10a) — the machine layer of the three-layer
user validation. They run against a LIVE server and assert that what the API actually serves is
the story `config/portfolio-demo.yaml` declares: the public projection, the dashboard aggregate,
the per-band transaction counts the Phase 3b deep links land on, the alert queue and its action
trail, and tenant-safe 404s.

Every expected value is READ from the configured story at test time. Nothing here types a count,
a band, an id, or an email: a test that hardcoded `3` open alerts would pass while the config said
something else, which is exactly the drift this layer exists to catch. The `story` fixture refuses
an EMPTY story for the same reason — against zero scenarios every assertion below would compare
`0` with `0` and report green over an empty tenant.

Marked `smoke`, so the default suite (`-m 'not smoke and not llm_live'`) never runs them. Two env
vars gate them, mirroring the existing smoke suite's `SMOKE_BASE_URL` convention:

* `SMOKE_BASE_URL` — the running server (`make portfolio-demo-smoke SMOKE_BASE_URL=<url>`).
* `PORTFOLIO_DEMO_SMOKE_ENABLED` — `true` only where the story is actually bootstrapped. The
  deploy workflow drives it from `vars.PORTFOLIO_DEMO_BOOTSTRAP_ENABLED`, so the same
  `pytest -m smoke` invocation stays correct against a deployment that carries no demo.

Authentication: `SMOKE_AUTH_TOKEN` supplies a bearer token when the target verifies JWTs. Without
it requests go tokenless, which only works where the non-prod dev bypass is on. An unauthorized
probe FAILS with that instruction rather than skipping — a silent skip would let an unreachable
demo look green.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import httpx
import pytest
from pydantic.alias_generators import to_camel

from fraudlens_backend.db.models.enums import AlertStatus, SarStatus
from fraudlens_backend.portfolio_demo import PortfolioDemoConfig, load_portfolio_demo_config
from fraudlens_backend.portfolio_demo.verification import UNSCORED_LABEL
from fraudlens_core import RiskBand

pytestmark = pytest.mark.smoke

BASE_URL = os.environ.get("SMOKE_BASE_URL", "")
STORY_ENABLED = os.environ.get("PORTFOLIO_DEMO_SMOKE_ENABLED", "").strip().lower() == "true"
AUTH_TOKEN = os.environ.get("SMOKE_AUTH_TOKEN", "").strip()

_API = "/api/v1"
_TIMEOUT = 30.0
_OK = 200
_NOT_FOUND = 404
_UNAUTHORIZED = (401, 403)

requires_live_story = pytest.mark.skipif(
    not (BASE_URL and STORY_ENABLED),
    reason="SMOKE_BASE_URL and PORTFOLIO_DEMO_SMOKE_ENABLED=true are required",
)


@pytest.fixture(scope="module")
def story() -> PortfolioDemoConfig:
    """Return the configured story, refusing an empty one so no assertion can be vacuous."""
    config = load_portfolio_demo_config()
    if not config.scenarios or not config.expected.transactions:
        pytest.fail(
            "the configured portfolio demo story is EMPTY — every assertion in this suite would "
            "compare 0 with 0 and report green over an empty tenant. Pin the story first."
        )
    return config


@pytest.fixture(scope="module")
def client() -> Iterator[httpx.Client]:
    """Yield a client for the live target, failing fast when it will not authenticate us."""
    headers = {"Authorization": f"Bearer {AUTH_TOKEN}"} if AUTH_TOKEN else {}
    with httpx.Client(base_url=BASE_URL, headers=headers, timeout=_TIMEOUT) as http:
        probe = http.get(f"{_API}/dashboard/metrics")
        if probe.status_code in _UNAUTHORIZED:
            pytest.fail(
                f"{BASE_URL} rejected the smoke session ({probe.status_code}). Set "
                "SMOKE_AUTH_TOKEN=<bearer token>, or point SMOKE_BASE_URL at a non-prod target "
                "whose dev bypass is enabled."
            )
        yield http


def _json(response: httpx.Response) -> dict[str, object]:
    """Return a successful response's JSON body, asserting the status first."""
    assert response.status_code == _OK, f"{response.request.url} -> {response.status_code}"
    body = response.json()
    assert isinstance(body, dict)
    return body


def _alerts_with_status(client: httpx.Client, status: AlertStatus) -> list[dict[str, object]]:
    """Return the demo tenant's alerts in one lifecycle status."""
    body = _json(client.get(f"{_API}/alerts", params={"status": status.value}))
    alerts = body["alerts"]
    assert isinstance(alerts, list)
    return alerts


def _scenario_bands(story: PortfolioDemoConfig, status: AlertStatus) -> list[str]:
    """Return the expected severities of the alerts configured to end in one status."""
    return sorted(
        scenario.expected_risk_band.value
        for scenario in story.scenarios
        if scenario.alert_target is status and scenario.expected_risk_band is not None
    )


@requires_live_story
def test_story_is_pinned_and_non_empty(story: PortfolioDemoConfig) -> None:
    """The story must declare real work, or the rest of this suite proves nothing."""
    assert len(story.scenarios) == story.expected.transactions
    assert story.scored_scenarios, "no scenario is scored, so no band can be asserted"
    assert sum(story.expected.risk_bands.values()) > 0
    assert sum(story.expected.alert_states.values()) > 0
    assert sum(story.expected.sar_states.values()) > 0


@requires_live_story
def test_ops_probes_are_healthy() -> None:
    """A demo whose database is not ready cannot be serving the pinned story."""
    assert httpx.get(f"{BASE_URL}/healthz", timeout=_TIMEOUT).status_code == _OK
    assert httpx.get(f"{BASE_URL}/readyz", timeout=_TIMEOUT).status_code == _OK


@requires_live_story
def test_projection_serves_the_configured_identity(
    client: httpx.Client, story: PortfolioDemoConfig
) -> None:
    """The unauthenticated projection returns exactly the configured agency and personas."""
    body = _json(client.get(f"{_API}/portfolio-demo/config"))
    assert body["storyVersion"] == story.story_version
    assert body["agency"] == {
        "id": str(story.agency.id),
        "name": story.agency.name,
        "slug": story.agency.slug,
        "researchPartitionKey": story.agency.research_partition_key,
    }
    personas = body["personas"]
    assert isinstance(personas, list)
    assert [persona["key"] for persona in personas] == [p.key for p in story.personas]
    assert [
        (persona["role"], persona["email"], persona["displayName"], persona["pickerTag"])
        for persona in personas
    ] == [(p.role.value, p.email, p.display_name, p.picker_tag) for p in story.personas]


@requires_live_story
def test_projection_omits_every_private_story_value(
    client: httpx.Client, story: PortfolioDemoConfig
) -> None:
    """Seed ids, authored payloads, expected outcomes, and review notes never leave the backend."""
    response = client.get(f"{_API}/portfolio-demo/config")
    body = _json(response)
    assert set(body) == {"storyVersion", "agency", "personas", "syntheticPassword"}

    # (category, value) pairs: a failure reports the CATEGORY that leaked, never the value, the
    # same way `PortfolioDemoConfigError` reports a field location rather than its content.
    private: list[tuple[str, str]] = [
        ("model.version_label", story.model.version_label),
        ("workflow.resolution_note", story.workflow.resolution_note),
        ("workflow.approval_note", story.workflow.approval_note),
        ("workflow.rejection_note", story.workflow.rejection_note),
        *(("persona.seed_user_id", str(persona.seed_user_id)) for persona in story.personas),
    ]
    for scenario in story.scenarios:
        private.extend(
            (
                ("scenario.external_id", story.external_id(scenario)),
                ("scenario.scenario_id", scenario.scenario_id),
                ("transaction.origin_account", scenario.transaction.origin_account),
                ("transaction.dest_account", scenario.transaction.dest_account),
            )
        )
    leaked = sorted({category for category, value in private if value in response.text})
    assert not leaked, f"the public projection leaked these private categories: {leaked}"


@requires_live_story
def test_dashboard_metrics_match_the_configured_distribution(
    client: httpx.Client, story: PortfolioDemoConfig
) -> None:
    """Every dashboard counter equals the pinned expectation, band by band and state by state."""
    metrics = _json(client.get(f"{_API}/dashboard/metrics"))

    transactions = metrics["transactions"]
    assert isinstance(transactions, dict)
    assert transactions["total"] == story.expected.transactions
    bands = transactions["byRiskBand"]
    assert isinstance(bands, dict)
    assert {band.value: bands.get(band.value, 0) for band in RiskBand} == {
        band.value: story.expected.risk_bands.get(band, 0) for band in RiskBand
    }
    assert bands.get(UNSCORED_LABEL, 0) == story.expected.unscored

    alerts = metrics["alerts"]
    assert isinstance(alerts, dict)
    assert {to_camel(status.value): alerts[to_camel(status.value)] for status in AlertStatus} == {
        to_camel(status.value): story.expected.alert_states.get(status, 0) for status in AlertStatus
    }
    assert alerts["total"] == sum(story.expected.alert_states.values())

    sar = metrics["sar"]
    assert isinstance(sar, dict)
    assert {to_camel(status.value): sar[to_camel(status.value)] for status in SarStatus} == {
        to_camel(status.value): story.expected.sar_states.get(status, 0) for status in SarStatus
    }
    assert sar["total"] == sum(story.expected.sar_states.values())

    model_health = metrics["modelHealth"]
    assert isinstance(model_health, dict)
    assert model_health["activeVersionLabel"] == story.model.version_label


@requires_live_story
def test_each_risk_band_filter_returns_its_configured_count(
    client: httpx.Client, story: PortfolioDemoConfig
) -> None:
    """`?riskBand=` returns the configured count per band — the Phase 3b deep links land on data."""
    totals = {
        band.value: _json(client.get(f"{_API}/transactions", params={"riskBand": band.value}))[
            "total"
        ]
        for band in RiskBand
    }
    assert totals == {band.value: story.expected.risk_bands.get(band, 0) for band in RiskBand}


@requires_live_story
def test_alert_queue_matches_configured_states_and_severities(
    client: httpx.Client, story: PortfolioDemoConfig
) -> None:
    """Each configured alert status holds its configured count, at its scenario's band."""
    for status, expected_count in story.expected.alert_states.items():
        alerts = _alerts_with_status(client, status)
        assert len(alerts) == expected_count, f"alerts in '{status.value}'"
        assert sorted(str(alert["severity"]) for alert in alerts) == _scenario_bands(story, status)


@requires_live_story
def test_resolved_alerts_expose_the_action_trail_that_moved_them(
    client: httpx.Client, story: PortfolioDemoConfig
) -> None:
    """Each configured `resolved` alert carries the append-only trail that moved it there.

    The trail is NOT asserted to be an ordered chain: `comment` is a legitimate action that
    records `from == to` and may be appended after the transition (the bootstrap records the SAR
    decision note that way, because `review_sar` persists no free text of its own). What must
    hold is that every action reports real statuses and that one of them actually reached
    `resolved`.
    """
    statuses = {status.value for status in AlertStatus}
    if not story.expected.alert_states.get(AlertStatus.RESOLVED):
        pytest.skip("the configured story resolves no alert")
    for alert in _alerts_with_status(client, AlertStatus.RESOLVED):
        detail = _json(client.get(f"{_API}/alerts/{alert['alertId']}"))
        actions = detail["actions"]
        assert isinstance(actions, list)
        assert actions, "a resolved alert must expose the actions that resolved it"
        for action in actions:
            assert action["fromStatus"] in statuses
            assert action["toStatus"] in statuses
            assert action["actorId"], "every recorded action names its actor"
        assert any(action["toStatus"] == AlertStatus.RESOLVED.value for action in actions), (
            "no action in the trail actually reached `resolved`"
        )


@requires_live_story
def test_in_review_alerts_expose_their_assignee(
    client: httpx.Client, story: PortfolioDemoConfig
) -> None:
    """`ASSIGN` is the only route into `in_review`, so each one must name who it moved to."""
    if not story.expected.alert_states.get(AlertStatus.IN_REVIEW):
        pytest.skip("the configured story leaves no alert in review")
    for alert in _alerts_with_status(client, AlertStatus.IN_REVIEW):
        assigned_to = alert["assignedTo"]
        assert assigned_to, "an in_review alert must expose the assignee ASSIGN moved it to"
        uuid.UUID(str(assigned_to))


@requires_live_story
def test_unknown_ids_are_tenant_safe_404s(client: httpx.Client) -> None:
    """An id outside the tenant resolves to 404 with an error envelope, never another row."""
    stranger = uuid.uuid4()
    for path in (f"{_API}/alerts/{stranger}", f"{_API}/transactions/{stranger}"):
        response = client.get(path)
        assert response.status_code == _NOT_FOUND, path
        body = response.json()
        assert set(body) >= {"code", "message", "requestId"}
        assert str(stranger) not in str(body["message"])
