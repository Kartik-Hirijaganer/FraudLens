"""Behavioral tests for the read-only Supabase network/TLS security checker."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pytest


def _load_checker() -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "check_supabase_security.py"
    spec = spec_from_file_location("check_supabase_security_for_test", script_path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check_supabase_security = _load_checker()
NetworkRestrictionsResponse = check_supabase_security.NetworkRestrictionsResponse
SslEnforcementResponse = check_supabase_security.SslEnforcementResponse
find_violations = check_supabase_security.find_violations


def _network(
    ipv4: Sequence[str],
    ipv6: Sequence[str] = (),
    *,
    status: str = "applied",
) -> NetworkRestrictionsResponse:
    return NetworkRestrictionsResponse.model_validate(
        {
            "config": {
                "dbAllowedCidrs": list(ipv4),
                "dbAllowedCidrsV6": list(ipv6),
            },
            "entitlement": "allowed",
            "status": status,
        }
    )


def _ssl(*, enabled: bool = True, applied: bool = True) -> SslEnforcementResponse:
    return SslEnforcementResponse.model_validate(
        {
            "appliedSuccessfully": applied,
            "currentConfig": {"database": enabled},
        }
    )


def test_least_privilege_network_and_tls_pass() -> None:
    assert find_violations(_network(("203.0.113.7/32",), ("2001:db8::/64",)), _ssl()) == []


def test_default_routes_fail_without_echoing_cidrs() -> None:
    violations = find_violations(_network(("0.0.0.0/0",), ("::/0",)), _ssl())

    assert violations == [
        "IPv4 allowlist contains a default route",
        "IPv6 allowlist contains a default route",
    ]
    assert all("0.0.0.0" not in violation and "::" not in violation for violation in violations)


def test_unapplied_empty_network_and_disabled_tls_fail_closed() -> None:
    violations = find_violations(
        _network((), status="pending"),
        _ssl(enabled=False, applied=False),
    )

    assert "database network restrictions are not applied" in violations
    assert "database allowlist is empty, which Supabase treats as unrestricted" in violations
    assert "database TLS enforcement configuration was not applied" in violations
    assert "external database connections do not require TLS" in violations


def test_unexpectedly_broad_cidrs_fail() -> None:
    violations = find_violations(_network(("10.0.0.0/8",), ("2001:db8::/48",)), _ssl())

    assert violations == [
        "IPv4 allowlist entry 1 is broader than /24",
        "IPv6 allowlist entry 1 is broader than /64",
    ]


def test_invalid_cli_json_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    completed = subprocess.CompletedProcess(
        args=["supabase"],
        returncode=0,
        stdout="not-json",
        stderr="",
    )
    monkeypatch.setattr(
        check_supabase_security.subprocess,
        "run",
        lambda *args, **kwargs: completed,
    )

    with pytest.raises(RuntimeError, match="invalid security response"):
        check_supabase_security._run_json(
            ("supabase", "network-restrictions", "get"),
            NetworkRestrictionsResponse,
        )


def test_main_reports_success_without_printing_cidrs(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    responses = iter((_network(("203.0.113.7/32",)), _ssl()))
    monkeypatch.setattr(check_supabase_security, "_run_json", lambda *args: next(responses))

    assert check_supabase_security.main(("--project-ref", "project-ref")) == 0
    output = capsys.readouterr().out
    assert "1 least-privilege database CIDR(s); TLS required" in output
    assert "203.0.113.7" not in output


def test_main_requires_project_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_PROJECT_REF", raising=False)

    assert check_supabase_security.main(()) == 2
