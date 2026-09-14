"""Summary: In-memory Kubernetes Secret synchronization with explicit context and key allowlists.

Key classes:
- SecretSet: validated Secret name and redacted environment material.

Key functions:
- load_secret_sets: read only allowlisted runtime keys from the process environment.
- sync_secrets: apply generated Secret manifests without writing values to disk or logs.

Notes:
- Values are Pydantic SecretStr instances and command errors never include kubectl input data.
"""

from __future__ import annotations

import os

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from lib.k8s_demo.config import K8sDemoConfig
from lib.k8s_demo.kubectl import Kubectl

_SECRET_KEYS = {
    "fraudlens-backend-secrets": (
        "DATABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "FRAUDLENS_AUTH_JWKS_URL",
        "FRAUDLENS_AUTH_JWT_ISSUER",
    ),
    "fraudlens-llm-secrets": ("OPENROUTER_API_KEY",),
}


class SecretSet(BaseModel):
    """One Kubernetes Secret name plus allowlisted values kept redacted in representations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., description="Destination Kubernetes Secret name.")
    values: dict[str, SecretStr] = Field(
        ..., min_length=1, repr=False, description="Secret values."
    )


def load_secret_sets() -> list[SecretSet]:
    """Read complete allowlisted Secret groups; fail without naming missing values' contents."""
    groups: list[SecretSet] = []
    missing: list[str] = []
    for name, keys in _SECRET_KEYS.items():
        values: dict[str, SecretStr] = {}
        for key in keys:
            value = os.environ.get(key, "").strip()
            if value:
                values[key] = SecretStr(value)
            elif key in {"DATABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"}:
                missing.append(key)
        if values:
            groups.append(SecretSet(name=name, values=values))
    if missing:
        raise ValueError(f"required secret keys are missing: {', '.join(sorted(missing))}")
    return groups


def sync_secrets(config: K8sDemoConfig, *, confirmed: bool) -> list[str]:
    """Apply allowlisted runtime Secrets directly over stdin after the non-kind mutation gate."""
    kubectl = Kubectl(config)
    applied: list[str] = []
    for group in load_secret_sets():
        document = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": group.name, "namespace": config.namespace},
            "type": "Opaque",
            "stringData": {key: value.get_secret_value() for key, value in group.values.items()},
        }
        kubectl.apply(
            yaml.safe_dump(document, sort_keys=False), platform="aks", confirmed=confirmed
        )
        applied.append(group.name)
    return applied
