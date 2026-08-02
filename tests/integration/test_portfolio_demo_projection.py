"""The public portfolio-demo projection: gated off by default, and public fields ONLY.

`GET /api/v1/portfolio-demo/config` is the one unauthenticated demo surface, so these tests pin
both halves of its contract: it 404s unless the demo is explicitly enabled (and stays 404 in prod
when only the dev-bypass flag is set, because the bypass is prod-inert), and its payload carries
nothing beyond the login screen's needs. The "never returns" half is asserted against the WHOLE
serialized body — not field-by-field — so a future key added to the response model, or a projection
rewritten to dump the config, fails here instead of leaking seeded ids, authored transactions,
pinned expectations, or review text.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from fraudlens_backend.portfolio_demo import PortfolioDemoConfig, load_portfolio_demo_config

_ROUTE = "/api/v1/portfolio-demo/config"
_SYNTHETIC_PASSWORD = "synthetic-projection-test-password"


@pytest.fixture
def config() -> PortfolioDemoConfig:
    """Return the committed story the projection must mirror."""
    return load_portfolio_demo_config()


def test_the_projection_is_absent_until_the_demo_is_enabled(
    client_factory: Callable[..., TestClient],
) -> None:
    """With neither portfolio mode nor the bypass, the route does not exist (404 envelope)."""
    client = client_factory(portfolio_demo_enabled=False, auth_dev_bypass=False)
    response = client.get(_ROUTE)
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "not_found"
    assert set(body) == {"code", "message", "details", "requestId"}


def test_the_prod_bypass_flag_alone_does_not_expose_the_projection(
    client_factory: Callable[..., TestClient],
) -> None:
    """In prod the dev bypass is inert, so only the explicit portfolio gate can serve the route."""
    client = client_factory(environment="prod", auth_dev_bypass=True, portfolio_demo_enabled=False)
    assert client.get(_ROUTE).status_code == 404


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"portfolio_demo_enabled": True}, id="portfolio-mode"),
        pytest.param(
            {"portfolio_demo_enabled": False, "auth_dev_bypass": True}, id="non-prod-bypass"
        ),
    ],
)
def test_either_gate_serves_the_projection(
    client_factory: Callable[..., TestClient], overrides: dict[str, object]
) -> None:
    """Portfolio mode and the non-prod dev bypass each make the picker's data available."""
    client = client_factory(**overrides)
    assert client.get(_ROUTE).status_code == 200


def test_the_projection_returns_the_configured_agency_and_personas(
    client_factory: Callable[..., TestClient], config: PortfolioDemoConfig
) -> None:
    """Every public value comes from the story config, in configured order."""
    client = client_factory(portfolio_demo_enabled=True, demo_auth_password=_SYNTHETIC_PASSWORD)
    body = client.get(_ROUTE).json()

    assert body["storyVersion"] == config.story_version
    assert body["agency"] == {
        "id": str(config.agency.id),
        "name": config.agency.name,
        "slug": config.agency.slug,
        "researchPartitionKey": config.agency.research_partition_key,
    }
    assert body["syntheticPassword"] == _SYNTHETIC_PASSWORD
    assert [persona["key"] for persona in body["personas"]] == [
        persona.key for persona in config.personas
    ]
    assert body["personas"][0] == {
        "key": config.personas[0].key,
        "role": config.personas[0].role.value,
        "email": config.personas[0].email,
        "displayName": config.personas[0].display_name,
        "initials": config.personas[0].initials,
        "pickerName": config.personas[0].picker_name,
        "pickerTag": config.personas[0].picker_tag,
        "pickerAccent": config.personas[0].picker_accent,
    }


def test_an_unconfigured_credential_degrades_to_an_empty_password(
    client_factory: Callable[..., TestClient],
) -> None:
    """No configured credential disables the picker's auto-fill; it never fails the screen."""
    client = client_factory(portfolio_demo_enabled=True, demo_auth_password=None)
    body = client.get(_ROUTE).json()
    assert body["syntheticPassword"] == ""
    assert body["personas"], "personas must still be listed so the picker can render"


def test_the_projection_exposes_only_the_public_field_set(
    client_factory: Callable[..., TestClient],
) -> None:
    """The response shape is an allowlist: no key may appear that the picker does not need."""
    client = client_factory(portfolio_demo_enabled=True)
    body = client.get(_ROUTE).json()
    assert set(body) == {"storyVersion", "agency", "personas", "syntheticPassword"}
    assert set(body["agency"]) == {"id", "name", "slug", "researchPartitionKey"}
    for persona in body["personas"]:
        assert set(persona) == {
            "key",
            "role",
            "email",
            "displayName",
            "initials",
            "pickerName",
            "pickerTag",
            "pickerAccent",
        }


def test_the_projection_never_leaks_the_private_half_of_the_story(
    client_factory: Callable[..., TestClient], config: PortfolioDemoConfig
) -> None:
    """Seeded ids, authored payloads, pinned expectations, and review text stay server-side.

    Asserted against the whole serialized body so a new response field cannot smuggle any of
    them in, and against the CONFIGURED values so the test cannot pass by coincidence.
    """
    client = client_factory(portfolio_demo_enabled=True)
    payload = json.dumps(client.get(_ROUTE).json())

    forbidden: list[tuple[str, str]] = [
        *(("seed user id", str(persona.seed_user_id)) for persona in config.personas),
        ("model version label", config.model.version_label),
        ("workflow resolution note", config.workflow.resolution_note),
        ("workflow approval note", config.workflow.approval_note),
        ("workflow rejection note", config.workflow.rejection_note),
        ("external id namespace", config.external_id_namespace),
        ("llm mode", f'"{config.execution.llm_mode}"'),
        ("rag embedding mode", f'"{config.execution.rag_embedding_mode}"'),
    ]
    for scenario in config.scenarios:
        forbidden.append((f"external id ({scenario.scenario_id})", config.external_id(scenario)))
        forbidden.append((f"scenario id ({scenario.scenario_id})", scenario.scenario_id))
        forbidden.append(
            (f"origin account ({scenario.scenario_id})", scenario.transaction.origin_account)
        )
        forbidden.append(
            (f"dest account ({scenario.scenario_id})", scenario.transaction.dest_account)
        )
    for label, value in forbidden:
        assert value not in payload, f"projection leaked {label}"

    # Expected counts and per-scenario outcomes are pinned server-side only. `expected` totals
    # are small integers that could appear coincidentally, so assert on the KEYS instead.
    for key in ("expected", "riskBands", "alertStates", "sarStates", "scenarios", "probe"):
        assert key not in payload
