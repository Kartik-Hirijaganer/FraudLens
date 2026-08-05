"""Summary: Read-only Supabase control-plane guard for database network and TLS posture.
It calls the authenticated Supabase CLI, validates its JSON responses with Pydantic, and
fails when database restrictions are unapplied, a default route is allowed, an allowlist
range is unexpectedly broad, or external database connections can avoid TLS. It never
prints CIDRs or credentials. Run with `make supabase-security-check`.

Key classes:
- NetworkRestrictionConfig: validated IPv4/IPv6 database allowlists.
- NetworkRestrictionsResponse: validated Supabase network-restriction response.
- SslDatabaseConfig: validated database TLS switch.
- SslEnforcementResponse: validated Supabase TLS response.

Key functions:
- find_violations: return least-privilege network/TLS posture violations.
- main: fetch live control-plane state and return a process exit code.

Notes:
- The project reference is non-secret and must be supplied explicitly or through
  `SUPABASE_PROJECT_REF`; the Supabase CLI supplies authentication from its own profile.
- HTTPS APIs such as Auth/PostgREST are outside database network restrictions. Data API
  isolation is enforced separately by Alembic RLS/grant hardening.
"""

from __future__ import annotations

import argparse
import os
import subprocess
from collections.abc import Sequence
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field, IPvAnyNetwork, ValidationError

_MIN_IPV4_PREFIX = 24
_MIN_IPV6_PREFIX = 64
_COMMAND_TIMEOUT_SECONDS = 30
_IPV4_VERSION = 4
_IPV6_VERSION = 6


class NetworkRestrictionConfig(BaseModel):
    """Supabase database CIDR allowlists."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    ipv4_cidrs: list[IPvAnyNetwork] = Field(
        default_factory=list,
        alias="dbAllowedCidrs",
        description="IPv4 CIDRs allowed to connect to Postgres and its pooler.",
    )
    ipv6_cidrs: list[IPvAnyNetwork] = Field(
        default_factory=list,
        alias="dbAllowedCidrsV6",
        description="IPv6 CIDRs allowed to connect to Postgres and its pooler.",
    )


class NetworkRestrictionsResponse(BaseModel):
    """Validated network-restriction control-plane response."""

    model_config = ConfigDict(extra="ignore")

    config: NetworkRestrictionConfig = Field(
        ...,
        description="Applied database CIDR allowlists.",
    )
    entitlement: str = Field(
        ...,
        description="Whether the project tier permits network restrictions.",
    )
    status: str = Field(
        ...,
        description="Supabase application status for the network restriction change.",
    )


class SslDatabaseConfig(BaseModel):
    """Supabase database TLS enforcement switch."""

    model_config = ConfigDict(extra="ignore")

    database: bool = Field(
        ...,
        description="Whether external database clients must negotiate TLS.",
    )


class SslEnforcementResponse(BaseModel):
    """Validated TLS-enforcement control-plane response."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    applied_successfully: bool = Field(
        ...,
        alias="appliedSuccessfully",
        description="Whether Supabase successfully applied the TLS configuration.",
    )
    current_config: SslDatabaseConfig = Field(
        ...,
        alias="currentConfig",
        description="Current database TLS configuration.",
    )


ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


def _run_json(command: Sequence[str], model_type: type[ResponseModel]) -> ResponseModel:
    """Run one Supabase CLI read and validate its JSON without logging sensitive state."""
    try:
        result = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Supabase CLI is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Supabase CLI security read timed out") from exc
    if result.returncode != 0:
        raise RuntimeError("Supabase CLI security read failed")
    try:
        return model_type.model_validate_json(result.stdout)
    except ValidationError as exc:
        raise RuntimeError("Supabase CLI returned an invalid security response") from exc


def find_violations(
    network: NetworkRestrictionsResponse,
    ssl: SslEnforcementResponse,
    *,
    min_ipv4_prefix: int = _MIN_IPV4_PREFIX,
    min_ipv6_prefix: int = _MIN_IPV6_PREFIX,
) -> list[str]:
    """Return database network/TLS violations without including the actual CIDRs."""
    violations: list[str] = []
    if network.status.casefold() != "applied":
        violations.append("database network restrictions are not applied")

    cidrs = [*network.config.ipv4_cidrs, *network.config.ipv6_cidrs]
    if not cidrs:
        violations.append("database allowlist is empty, which Supabase treats as unrestricted")

    for index, cidr in enumerate(network.config.ipv4_cidrs, start=1):
        if cidr.version != _IPV4_VERSION:
            violations.append(f"IPv4 allowlist entry {index} is not IPv4")
        elif cidr.prefixlen == 0:
            violations.append("IPv4 allowlist contains a default route")
        elif cidr.prefixlen < min_ipv4_prefix:
            violations.append(f"IPv4 allowlist entry {index} is broader than /{min_ipv4_prefix}")

    for index, cidr in enumerate(network.config.ipv6_cidrs, start=1):
        if cidr.version != _IPV6_VERSION:
            violations.append(f"IPv6 allowlist entry {index} is not IPv6")
        elif cidr.prefixlen == 0:
            violations.append("IPv6 allowlist contains a default route")
        elif cidr.prefixlen < min_ipv6_prefix:
            violations.append(f"IPv6 allowlist entry {index} is broader than /{min_ipv6_prefix}")

    if not ssl.applied_successfully:
        violations.append("database TLS enforcement configuration was not applied")
    if not ssl.current_config.database:
        violations.append("external database connections do not require TLS")
    return violations


def _parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-ref",
        default=os.environ.get("SUPABASE_PROJECT_REF"),
        help="Supabase project reference (or set SUPABASE_PROJECT_REF).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Audit live Supabase network/TLS controls; print no CIDRs and return an exit code."""
    args = _parser().parse_args(argv)
    if not args.project_ref:
        print("check_supabase_security FAILED: --project-ref is required")
        return 2

    try:
        network = _run_json(
            (
                "supabase",
                "network-restrictions",
                "get",
                "--project-ref",
                args.project_ref,
                "--output",
                "json",
            ),
            NetworkRestrictionsResponse,
        )
        ssl = _run_json(
            (
                "supabase",
                "ssl-enforcement",
                "get",
                "--project-ref",
                args.project_ref,
                "--experimental",
                "--output",
                "json",
            ),
            SslEnforcementResponse,
        )
    except RuntimeError as exc:
        print(f"check_supabase_security FAILED: {exc}")
        return 2

    violations = find_violations(network, ssl)
    for violation in violations:
        print(violation)
    if violations:
        print(f"check_supabase_security FAILED: {len(violations)} violation(s)")
        return 1

    cidr_count = len(network.config.ipv4_cidrs) + len(network.config.ipv6_cidrs)
    print(
        f"check_supabase_security OK: {cidr_count} least-privilege database CIDR(s); TLS required"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
