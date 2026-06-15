"""Fail-closed authorization gate (plan §16 Phase 13, §6): the consolidated proof that the
whole business + admin surface denies access by default. Covers the prod dev-bypass being inert
at the HTTP layer, an unauthenticated sweep across every representative resource (incl. the
admin model APIs), and a non-admin being forbidden on an admin-only route."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

# Representative authed GET routes across the surface; each resolves auth BEFORE the DB session,
# so a missing token fails closed at 401 without the database being configured.
_AUTHED_ROUTES = (
    "/api/v1/transactions",
    "/api/v1/alerts",
    "/api/v1/rules",
    "/api/v1/dashboard/metrics",
    "/api/v1/model-versions",
    "/api/v1/training-runs",  # admin-only
    "/api/v1/drift-reports",  # admin-only
)


def test_dev_bypass_is_inert_in_prod_over_http(
    client_factory: Callable[..., TestClient],
) -> None:
    # Even with the bypass flag ON, prod must require a real token (is_dev_bypass_enabled is False
    # whenever environment == "prod"), so an unauthenticated request fails closed.
    client = client_factory(environment="prod", auth_dev_bypass=True)
    assert client.get("/api/v1/dashboard/metrics").status_code == 401


def test_every_resource_requires_authentication(
    client_factory: Callable[..., TestClient],
) -> None:
    client = client_factory()  # no dev bypass, no token, fail-closed verifier
    codes = {route: client.get(route).status_code for route in _AUTHED_ROUTES}
    assert all(code == 401 for code in codes.values()), codes


def test_admin_routes_forbid_non_admins(
    client_factory: Callable[..., TestClient],
) -> None:
    # The dev bypass mints a NON-admin role, so the admin-only model API fails closed at 403.
    client = client_factory(environment="dev", auth_dev_bypass=True, auth_dev_bypass_role="analyst")
    response = client.get("/api/v1/training-runs")
    assert response.status_code == 403
    assert response.json()["code"] == "admin_role_required"
