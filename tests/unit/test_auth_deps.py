"""Unit tests for authentication dependency helpers."""

from __future__ import annotations

from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from fraudlens_backend.api.deps import CredentialsError, JwksTokenVerifier
from fraudlens_backend.settings import AppSettings


class _SigningKey:
    """Minimal PyJWKClient signing-key stand-in."""

    def __init__(self, key: Any) -> None:
        """Store the public key object PyJWT expects."""
        self.key = key


def _rsa_key() -> rsa.RSAPrivateKey:
    """Generate a short-lived RSA key for RS256 verifier tests."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _ec_key() -> ec.EllipticCurvePrivateKey:
    """Generate a short-lived P-256 key for ES256 verifier tests (Supabase's default)."""
    return ec.generate_private_key(ec.SECP256R1())


def _verifier_with_key(
    monkeypatch: pytest.MonkeyPatch,
    public_key: Any,
    **settings_overrides: Any,
) -> JwksTokenVerifier:
    """Build a JWKS verifier whose JWKS lookup returns the supplied key."""
    # These helpers mint RSA/RS256 tokens by default; pin the verifier to RS256 unless a test
    # overrides it (the production default is ES256, Supabase's asymmetric signing algorithm).
    settings_overrides.setdefault("auth_jwt_algorithm", "RS256")
    verifier = JwksTokenVerifier(
        AppSettings(
            environment="dev",
            auth_jwks_url="https://jwks.localhost.invalid/keys",
            **settings_overrides,
        )
    )
    monkeypatch.setattr(
        verifier._client,
        "get_signing_key_from_jwt",
        lambda _token: _SigningKey(public_key),
    )
    return verifier


def test_jwks_token_verifier_accepts_rs256_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    key = _rsa_key()
    verifier = _verifier_with_key(
        monkeypatch,
        key.public_key(),
        auth_jwt_issuer="issuer",
        auth_jwt_audience="fraudlens",
    )
    token = jwt.encode(
        {
            "sub": "22222222-2222-4222-8222-222222222222",
            "agency_id": "11111111-1111-4111-8111-111111111111",
            "user_role": "admin",
            "iss": "issuer",
            "aud": "fraudlens",
        },
        key,
        algorithm="RS256",
    )

    claims = verifier(token)

    assert claims.agency_id == "11111111-1111-4111-8111-111111111111"
    assert claims.user_id == "22222222-2222-4222-8222-222222222222"
    assert claims.role == "admin"


def test_jwks_token_verifier_accepts_es256_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    key = _ec_key()
    verifier = _verifier_with_key(
        monkeypatch,
        key.public_key(),
        auth_jwt_algorithm="ES256",
        auth_jwt_issuer="issuer",
        auth_jwt_audience="fraudlens",
    )
    token = jwt.encode(
        {
            "sub": "22222222-2222-4222-8222-222222222222",
            "agency_id": "11111111-1111-4111-8111-111111111111",
            "user_role": "admin",
            "iss": "issuer",
            "aud": "fraudlens",
        },
        key,
        algorithm="ES256",
    )

    claims = verifier(token)

    assert claims.agency_id == "11111111-1111-4111-8111-111111111111"
    assert claims.user_id == "22222222-2222-4222-8222-222222222222"
    assert claims.role == "admin"


def test_jwks_token_verifier_accepts_server_owned_app_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = _ec_key()
    verifier = _verifier_with_key(
        monkeypatch,
        key.public_key(),
        auth_jwt_algorithm="ES256",
        auth_jwt_issuer="issuer",
        auth_jwt_audience="fraudlens",
    )
    token = jwt.encode(
        {
            "sub": "22222222-2222-4222-8222-222222222222",
            "app_metadata": {
                "agency_id": "11111111-1111-4111-8111-111111111111",
                "user_role": "reviewer",
            },
            "iss": "issuer",
            "aud": "fraudlens",
        },
        key,
        algorithm="ES256",
    )

    claims = verifier(token)

    assert claims.agency_id == "11111111-1111-4111-8111-111111111111"
    assert claims.role == "reviewer"


def test_jwks_token_verifier_rejects_user_editable_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = _rsa_key()
    verifier = _verifier_with_key(monkeypatch, key.public_key())
    token = jwt.encode(
        {
            "sub": "22222222-2222-4222-8222-222222222222",
            "user_metadata": {
                "agency_id": "11111111-1111-4111-8111-111111111111",
                "user_role": "admin",
            },
        },
        key,
        algorithm="RS256",
    )

    with pytest.raises(CredentialsError, match="missing agency claim"):
        verifier(token)


def test_jwks_token_verifier_rejects_invalid_role(monkeypatch: pytest.MonkeyPatch) -> None:
    key = _rsa_key()
    verifier = _verifier_with_key(monkeypatch, key.public_key())
    token = jwt.encode(
        {
            "sub": "22222222-2222-4222-8222-222222222222",
            "agency_id": "11111111-1111-4111-8111-111111111111",
            "user_role": "superuser",
        },
        key,
        algorithm="RS256",
    )

    with pytest.raises(CredentialsError, match="invalid role claim"):
        verifier(token)


def test_jwks_token_verifier_accepts_auditor_role(monkeypatch: pytest.MonkeyPatch) -> None:
    key = _rsa_key()
    verifier = _verifier_with_key(monkeypatch, key.public_key())
    token = jwt.encode(
        {
            "sub": "22222222-2222-4222-8222-222222222222",
            "agency_id": "11111111-1111-4111-8111-111111111111",
            "user_role": "auditor",
        },
        key,
        algorithm="RS256",
    )

    assert verifier(token).role == "auditor"


def test_jwks_token_verifier_can_use_custom_role_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = _rsa_key()
    verifier = _verifier_with_key(monkeypatch, key.public_key(), auth_role_claim="role")
    token = jwt.encode(
        {
            "sub": "22222222-2222-4222-8222-222222222222",
            "agency_id": "11111111-1111-4111-8111-111111111111",
            "role": "reviewer",
        },
        key,
        algorithm="RS256",
    )

    assert verifier(token).role == "reviewer"
