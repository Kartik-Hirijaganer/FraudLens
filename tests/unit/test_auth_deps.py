"""Unit tests for authentication dependency helpers."""

from __future__ import annotations

from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

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


def _verifier_with_key(
    monkeypatch: pytest.MonkeyPatch,
    public_key: Any,
    **settings_overrides: Any,
) -> JwksTokenVerifier:
    """Build a JWKS verifier whose JWKS lookup returns the supplied key."""
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
            "role": "admin",
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


def test_jwks_token_verifier_rejects_invalid_role(monkeypatch: pytest.MonkeyPatch) -> None:
    key = _rsa_key()
    verifier = _verifier_with_key(monkeypatch, key.public_key())
    token = jwt.encode(
        {
            "sub": "22222222-2222-4222-8222-222222222222",
            "agency_id": "11111111-1111-4111-8111-111111111111",
            "role": "superuser",
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
            "role": "auditor",
        },
        key,
        algorithm="RS256",
    )

    assert verifier(token).role == "auditor"
