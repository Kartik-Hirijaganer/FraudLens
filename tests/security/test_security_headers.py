"""Security-header / CSP hardening tests (plan §16 Phase 13): the path-aware Content-Security-
Policy is the header Phase 13 adds (middleware/security.py). The four static headers are covered
by test_gateway.py; here we prove the CSP is strict on the API surface, relaxed only on the docs
UI (Swagger/ReDoc CDN), toggleable, and that every response now carries all five headers."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

from fraudlens_backend.middleware.security import content_security_policy, is_docs_path
from fraudlens_backend.settings import AppSettings

_CDN = "cdn.jsdelivr.net"
_ALL_SECURITY_HEADERS = {
    "content-security-policy",
    "x-content-type-options",
    "x-frame-options",
    "referrer-policy",
    "strict-transport-security",
}


def test_all_security_headers_including_csp_present(
    client_factory: Callable[..., TestClient],
) -> None:
    response = client_factory().get("/api/v1/health")
    assert _ALL_SECURITY_HEADERS.issubset({key.lower() for key in response.headers})


def test_api_surface_gets_strict_csp(client_factory: Callable[..., TestClient]) -> None:
    csp = client_factory().get("/api/v1/health").headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert _CDN not in csp  # the API surface never trusts the docs CDN


def test_docs_ui_gets_relaxed_csp_for_the_cdn(client_factory: Callable[..., TestClient]) -> None:
    response = client_factory().get("/docs")
    assert response.status_code == 200
    assert _CDN in response.headers["content-security-policy"]  # Swagger UI loads from the CDN


def test_csp_can_be_disabled(client_factory: Callable[..., TestClient]) -> None:
    response = client_factory(csp_enabled=False).get("/api/v1/health")
    assert "content-security-policy" not in {key.lower() for key in response.headers}


def test_content_security_policy_is_path_aware(
    make_settings: Callable[..., AppSettings],
) -> None:
    settings = make_settings()
    assert content_security_policy("/api/v1/health", settings) == settings.content_security_policy
    assert content_security_policy("/docs", settings) == settings.content_security_policy_docs


def test_docs_csp_falls_back_to_strict_when_unset(
    make_settings: Callable[..., AppSettings],
) -> None:
    settings = make_settings(content_security_policy_docs="")
    # An unset docs policy must NEVER weaken the API surface — it falls back to the strict policy.
    assert content_security_policy("/docs", settings) == settings.content_security_policy


def test_is_docs_path_matches_docs_ui_only(make_settings: Callable[..., AppSettings]) -> None:
    settings = make_settings()
    assert is_docs_path("/docs", settings)
    assert is_docs_path("/docs/oauth2-redirect", settings)
    assert is_docs_path("/redoc", settings)
    assert not is_docs_path("/api/v1/health", settings)
