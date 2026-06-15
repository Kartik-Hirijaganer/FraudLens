"""Integration tests for /api/v1: health, fail-closed auth + tenancy, error envelope."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from fraudlens_backend.api.deps import (
    AccessClaims,
    enforce_tenant,
    get_token_verifier,
)

AUTH_HEADER = {"Authorization": "Bearer test-token"}
ENVELOPE_KEYS = {"code", "message", "details", "requestId"}


def _accept_acme() -> Callable[[str], AccessClaims]:
    """A fake verifier that accepts any token and returns the 'acme' tenant claim."""
    return lambda _token: AccessClaims(agency_id="acme")


def test_api_health_returns_camelcase_heartbeat(
    client_factory: Callable[..., TestClient],
) -> None:
    response = client_factory().get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "FraudLens",
        "version": "1.0.0",
        "environment": "dev",
    }


def test_missing_token_fails_closed_401(client_factory: Callable[..., TestClient]) -> None:
    response = client_factory().get("/api/v1/agencies/acme")
    assert response.status_code == 401
    body = response.json()
    assert set(body) == ENVELOPE_KEYS
    assert body["code"] == "unauthorized"
    assert body["requestId"]


def test_invalid_token_is_401(client_factory: Callable[..., TestClient]) -> None:
    # No verifier override -> the default (unconfigured) verifier rejects every token.
    response = client_factory().get("/api/v1/agencies/acme", headers=AUTH_HEADER)
    assert response.status_code == 401
    assert response.json()["message"] == "invalid token"


def test_authorized_request_without_db_is_503(
    client_factory: Callable[..., TestClient],
) -> None:
    # Auth + tenancy pass (claim == path), but the agency lookup needs a database; the
    # client_factory app has no DATABASE_URL, so get_db_session fails closed with 503.
    # This also proves the dependency order: auth/tenancy resolve before the DB session.
    client = client_factory()
    client.app.dependency_overrides[get_token_verifier] = _accept_acme
    response = client.get("/api/v1/agencies/acme", headers=AUTH_HEADER)
    assert response.status_code == 503
    assert response.json()["code"] == "service_unavailable"


def test_valid_token_mismatched_tenant_is_403(
    client_factory: Callable[..., TestClient],
) -> None:
    client = client_factory()
    client.app.dependency_overrides[get_token_verifier] = _accept_acme
    response = client.get("/api/v1/agencies/other", headers=AUTH_HEADER)
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_dev_bypass_still_enforces_tenant_isolation(
    client_factory: Callable[..., TestClient],
) -> None:
    client = client_factory(environment="dev", auth_dev_bypass=True)
    response = client.get("/api/v1/agencies/other")  # bypass authN, but authZ mismatch
    assert response.status_code == 403


def test_dev_bypass_is_inert_in_prod(client_factory: Callable[..., TestClient]) -> None:
    client = client_factory(environment="prod", auth_dev_bypass=True)
    response = client.get("/api/v1/agencies/acme")  # no token; bypass must be inert
    assert response.status_code == 401


def test_unknown_path_is_404_envelope(client_factory: Callable[..., TestClient]) -> None:
    response = client_factory().get("/api/v1/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert set(body) == ENVELOPE_KEYS
    assert body["code"] == "not_found"


def test_validation_error_envelope_hides_raw_input(
    client_factory: Callable[..., TestClient],
) -> None:
    client = client_factory()
    app: FastAPI = client.app

    @app.get("/_test/validate")
    async def _validate(quantity: int) -> dict[str, int]:
        return {"quantity": quantity}

    response = client.get("/_test/validate", params={"quantity": "not-a-number"})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["details"]
    assert "quantity" in body["details"][0]["field"]
    assert all("not-a-number" not in str(detail) for detail in body["details"])


def test_internal_error_envelope_hides_internals(
    client_factory: Callable[..., TestClient],
) -> None:
    client = client_factory()
    app: FastAPI = client.app

    @app.get("/_test/boom")
    async def _boom() -> dict[str, str]:
        raise RuntimeError("kaboom secret detail")

    response = client.get("/_test/boom")
    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "internal_error"
    assert body["message"] == "An internal error occurred."
    assert "kaboom" not in str(body)
    assert "RuntimeError" not in str(body)


def test_unmapped_http_status_uses_generic_code(
    client_factory: Callable[..., TestClient],
) -> None:
    client = client_factory()
    app: FastAPI = client.app

    @app.get("/_test/teapot")
    async def _teapot() -> dict[str, str]:
        raise HTTPException(status_code=418, detail="i am a teapot")

    response = client.get("/_test/teapot")
    assert response.status_code == 418
    assert response.json()["code"] == "http_error"
    assert response.json()["message"] == "i am a teapot"


def test_nonstring_http_detail_is_humanized(
    client_factory: Callable[..., TestClient],
) -> None:
    client = client_factory()
    app: FastAPI = client.app

    @app.get("/_test/weird")
    async def _weird() -> dict[str, str]:
        raise HTTPException(status_code=418, detail={"unexpected": "shape"})

    response = client.get("/_test/weird")
    assert response.status_code == 418
    assert response.json()["message"] == "http error"


def test_enforce_tenant_missing_claim_maps_to_401() -> None:
    claims = AccessClaims.model_construct(agency_id="")  # bypass validation to simulate empty
    with pytest.raises(HTTPException) as excinfo:
        enforce_tenant(claims, "acme")
    assert excinfo.value.status_code == 401
