"""Behavioral tests for the post-deploy log-leakage gate.

The gate has to be strict about two opposite mistakes. It must catch a real leak — an injected
secret, a bearer token, PHI, or a traceback that reached a production log line. And it must never
become the leak itself: a finding reports a category or a variable NAME, never the value that
matched.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

import check_log_leakage
from check_log_leakage import JWT, PHI, STACK_TRACE, scan_log


def _synthetic_jwt() -> str:
    """Build a structurally valid but meaningless JWT.

    The token is assembled at run time rather than written as a literal so no committed file ever
    contains a credential-shaped string — the repo-wide `gitleaks` gate cannot distinguish a test
    fixture from the real thing, and it is right not to try.
    """

    def segment(claims: dict[str, str]) -> str:
        raw = json.dumps(claims, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return ".".join(
        (
            segment({"alg": "ES256"}),
            segment({"sub": "synthetic-subject"}),
            segment({"sig": "synthetic-signature"}),
        )
    )


_CLEAN = (
    '{"event": "request", "requestId": "1f0c", "route": "/api/v1/alerts", "status": 200}\n'
    '{"event": "investigation.completed", "runId": "0c3a", "riskBand": "high"}\n'
)
_SECRET = "postgresql+asyncpg://user:sup3r-secret@db.example.test/postgres"


def test_a_clean_log_reports_nothing_and_counts_what_it_read() -> None:
    report = scan_log(_CLEAN, {"DATABASE_URL": _SECRET})
    assert report.clean
    assert report.categories == ()
    assert report.secret_names == ()
    assert report.scanned_lines == 2


def test_an_injected_secret_is_reported_by_variable_name_only() -> None:
    report = scan_log(
        f'{_CLEAN}{{"error": "connect failed for {_SECRET}"}}\n', {"DATABASE_URL": _SECRET}
    )
    assert report.secret_names == ("DATABASE_URL",)
    # The report must be safe to paste into a public run log.
    assert _SECRET not in str(report.model_dump())


def test_a_bearer_token_echoed_into_a_log_line_is_caught() -> None:
    report = scan_log(f'{{"authorization": "Bearer {_synthetic_jwt()}"}}\n', {})
    assert JWT in report.categories


def test_a_rendered_traceback_is_caught() -> None:
    body = 'Traceback (most recent call last):\n  File "/app/main.py", line 1, in <module>\n'
    assert STACK_TRACE in scan_log(body, {}).categories


@pytest.mark.parametrize(
    "line",
    [
        '{"note": "subject 123-45-6789 flagged"}',
        '{"contact": "patient.name@example.test"}',
        '{"account": "4111111111111111"}',
    ],
)
def test_phi_the_gateway_claims_to_scrub_is_caught_when_it_reaches_a_log(line: str) -> None:
    # The categories come from the gateway's own scrubber, so this gate can never drift from
    # what the application promises to redact.
    assert PHI in scan_log(f"{line}\n", {}).categories


def test_an_already_redacted_line_is_not_a_finding() -> None:
    assert scan_log('{"note": "subject [REDACTED] flagged"}\n', {}).clean


def test_every_category_present_is_reported_together() -> None:
    body = (
        "Traceback (most recent call last):\n"
        '  File "/app/main.py", line 1, in <module>\n'
        '{"ssn": "123-45-6789"}\n'
        f'{{"token": "{_synthetic_jwt()}"}}\n'
    )
    assert scan_log(body, {}).categories == (JWT, PHI, STACK_TRACE)


# --- the CLI contract the deploy job relies on -----------------------------------------------


def _write(tmp_path: Path, body: str) -> Path:
    log = tmp_path / "revision.log"
    log.write_text(body, encoding="utf-8")
    return log


def test_the_cli_passes_a_clean_log(tmp_path: Path) -> None:
    log = _write(tmp_path, _CLEAN)
    exit_code = check_log_leakage.main(
        [str(log), "--secret-env", "DATABASE_URL"], environ={"DATABASE_URL": _SECRET}
    )
    assert exit_code == 0


def test_the_cli_fails_the_deploy_on_a_leak(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = _write(tmp_path, f"{_CLEAN}connect failed for {_SECRET}\n")
    exit_code = check_log_leakage.main(
        [str(log), "--secret-env", "DATABASE_URL"], environ={"DATABASE_URL": _SECRET}
    )
    assert exit_code == 1
    output = capsys.readouterr().out
    assert "DATABASE_URL" in output
    assert _SECRET not in output


def test_an_uninjected_secret_name_fails_instead_of_passing_vacuously(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Scanning for an empty value would match nothing and report a clean log — the most
    # dangerous possible outcome for a gate like this.
    log = _write(tmp_path, _CLEAN)
    exit_code = check_log_leakage.main(
        [str(log), "--secret-env", "DATABASE_URL"], environ={"DATABASE_URL": ""}
    )
    assert exit_code == 2
    assert "not injected" in capsys.readouterr().out


def test_an_unreadable_log_is_an_error_not_a_pass(tmp_path: Path) -> None:
    exit_code = check_log_leakage.main(
        [str(tmp_path / "absent.log"), "--secret-env", "DATABASE_URL"],
        environ={"DATABASE_URL": _SECRET},
    )
    assert exit_code == 2
