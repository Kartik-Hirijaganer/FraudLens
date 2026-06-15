"""Client-error telemetry tests (plan §5.3 endpoint 27): the sink requires a JWT, accepts a
scrubbed report (202), and rejects a body with no message (422). PHI scrubbing itself is
covered by the masking unit tests; here we exercise the endpoint's auth + branches."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

_PATH = "/api/v1/telemetry/client-error"


def test_client_error_accepted_with_context(client_factory: Callable[..., TestClient]) -> None:
    client = client_factory(environment="dev", auth_dev_bypass=True)
    resp = client.post(
        _PATH,
        json={"message": "boom while loading ssn 123-45-6789", "context": {"route": "/alerts"}},
    )
    assert resp.status_code == 202


def test_client_error_accepted_without_context(client_factory: Callable[..., TestClient]) -> None:
    client = client_factory(environment="dev", auth_dev_bypass=True)
    resp = client.post(_PATH, json={"message": "render failed"})
    assert resp.status_code == 202


def test_client_error_requires_message(client_factory: Callable[..., TestClient]) -> None:
    client = client_factory(environment="dev", auth_dev_bypass=True)
    resp = client.post(_PATH, json={"context": {"route": "/alerts"}})
    assert resp.status_code == 422


def test_client_error_fails_closed_without_token(
    client_factory: Callable[..., TestClient],
) -> None:
    resp = client_factory().post(_PATH, json={"message": "boom"})
    assert resp.status_code == 401
