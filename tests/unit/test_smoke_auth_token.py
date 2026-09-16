"""Behavioral tests for the persona token minter behind the authenticated production smoke.

Two properties matter more than the happy path. First, the credential must never travel anywhere a
process listing, a URL, or a log can hold it — so the request shape is asserted, not just its
result. Second, the token must be masked before it is written anywhere, or the very first line that
echoes it defeats the masking.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

import smoke_auth_token
from fraudlens_backend.portfolio_demo import load_portfolio_demo_config
from smoke_auth_token import (
    PersonaExport,
    SmokeTokenError,
    SupabaseSignIn,
    export_tokens,
    parse_exports,
)

_BASE_URL = "https://project.supabase.co"
_ANON_KEY = "publishable-anon-key"
_PASSWORD = "public-synthetic-password"
_TOKEN = "header.payload.signature"


@pytest.fixture
def story():
    return load_portfolio_demo_config()


def _transport(recorder: list[httpx.Request], *, status: int = 200, body: object | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        recorder.append(request)
        return httpx.Response(status, json=body if body is not None else {"access_token": _TOKEN})

    return httpx.MockTransport(handler)


def _mint(
    exports: tuple[PersonaExport, ...],
    recorder: list[httpx.Request],
    environment_file: Path | None,
    **kwargs: object,
) -> None:
    sign_in = SupabaseSignIn(base_url=_BASE_URL, anon_key=_ANON_KEY, password=_PASSWORD)
    with httpx.Client(transport=_transport(recorder, **kwargs)) as client:  # type: ignore[arg-type]
        export_tokens(client, exports, sign_in, environment_file)


def test_exports_resolve_to_the_configured_persona_emails(story) -> None:
    exports = parse_exports(["analyst=SMOKE_AUTH_TOKEN", "auditor=SMOKE_AUDITOR_TOKEN"], story)
    assert [export.email for export in exports] == [
        story.persona("analyst").email,
        story.persona("auditor").email,
    ]
    assert [export.env_name for export in exports] == ["SMOKE_AUTH_TOKEN", "SMOKE_AUDITOR_TOKEN"]


def test_an_unconfigured_persona_is_refused_by_name(story) -> None:
    with pytest.raises(SmokeTokenError) as error:
        parse_exports(["operator=SMOKE_AUTH_TOKEN"], story)
    assert "'operator' is not configured" in str(error.value)


@pytest.mark.parametrize("pair", ["analyst", "=SMOKE_AUTH_TOKEN", "analyst="])
def test_a_malformed_export_is_refused(pair: str, story) -> None:
    with pytest.raises(SmokeTokenError, match="<persona>=<ENV_NAME>"):
        parse_exports([pair], story)


def test_requesting_no_persona_is_an_error_not_a_silent_no_op(story) -> None:
    with pytest.raises(SmokeTokenError, match="at least one"):
        parse_exports([], story)


def test_the_password_travels_in_the_body_never_in_the_url(story) -> None:
    # A credential in a query string reaches access logs, proxies, and browser history — the
    # FraudLens no-secrets-in-URLs rule, applied to the deploy's own tooling.
    recorder: list[httpx.Request] = []
    _mint(parse_exports(["analyst=SMOKE_AUTH_TOKEN"], story), recorder, None)
    request = recorder[0]
    assert request.url.params["grant_type"] == "password"
    assert _PASSWORD not in str(request.url)
    assert json.loads(request.content)["password"] == _PASSWORD
    assert request.headers["apikey"] == _ANON_KEY


def test_the_token_is_masked_before_it_is_written_anywhere(
    story, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    environment_file = tmp_path / "github.env"
    _mint(parse_exports(["analyst=SMOKE_AUTH_TOKEN"], story), [], environment_file)
    output = capsys.readouterr().out
    assert f"::add-mask::{_TOKEN}" in output
    # The mask directive is the ONLY line carrying the value; nothing else echoes it.
    assert output.count(_TOKEN) == 1
    assert environment_file.read_text(encoding="utf-8") == f"SMOKE_AUTH_TOKEN={_TOKEN}\n"


def test_each_persona_is_exported_under_its_own_variable(story, tmp_path: Path) -> None:
    environment_file = tmp_path / "github.env"
    exports = parse_exports(["analyst=SMOKE_AUTH_TOKEN", "auditor=SMOKE_AUDITOR_TOKEN"], story)
    _mint(exports, [], environment_file)
    assert environment_file.read_text(encoding="utf-8").splitlines() == [
        f"SMOKE_AUTH_TOKEN={_TOKEN}",
        f"SMOKE_AUDITOR_TOKEN={_TOKEN}",
    ]


def test_a_rejected_sign_in_fails_without_quoting_the_response(story) -> None:
    with pytest.raises(SmokeTokenError) as error:
        _mint(parse_exports(["analyst=SMOKE_AUTH_TOKEN"], story), [], None, status=400)
    message = str(error.value)
    assert "HTTP 400" in message
    assert "may not be provisioned" in message


def test_a_response_without_a_token_is_not_treated_as_success(story) -> None:
    with pytest.raises(SmokeTokenError, match="no access token"):
        _mint(parse_exports(["analyst=SMOKE_AUTH_TOKEN"], story), [], None, body={})


@pytest.mark.parametrize(
    "missing", ["SUPABASE_URL", "VITE_SUPABASE_ANON_KEY", "FRAUDLENS_DEMO_AUTH_PASSWORD"]
)
def test_the_cli_refuses_to_run_with_an_uninjected_input(
    missing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in {
        "SUPABASE_URL": _BASE_URL,
        "VITE_SUPABASE_ANON_KEY": _ANON_KEY,
        "FRAUDLENS_DEMO_AUTH_PASSWORD": _PASSWORD,
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(missing, raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    monkeypatch.delenv("GITHUB_ENV", raising=False)
    assert smoke_auth_token.main(["--export", "analyst=SMOKE_AUTH_TOKEN"]) == 1


def test_the_cli_mints_into_the_github_environment_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    environment_file = tmp_path / "github.env"
    monkeypatch.setenv("SUPABASE_URL", f"{_BASE_URL}/")
    monkeypatch.setenv("SUPABASE_ANON_KEY", _ANON_KEY)
    monkeypatch.setenv("FRAUDLENS_DEMO_AUTH_PASSWORD", _PASSWORD)
    monkeypatch.setenv("GITHUB_ENV", str(environment_file))
    recorder: list[httpx.Request] = []
    real_client = httpx.Client
    monkeypatch.setattr(
        smoke_auth_token.httpx,
        "Client",
        lambda **kwargs: real_client(transport=_transport(recorder)),
    )
    assert smoke_auth_token.main(["--export", "analyst=SMOKE_AUTH_TOKEN"]) == 0
    # The trailing slash on the project URL must not produce a doubled path segment.
    assert str(recorder[0].url).startswith(f"{_BASE_URL}/auth/v1/token")
    assert environment_file.read_text(encoding="utf-8") == f"SMOKE_AUTH_TOKEN={_TOKEN}\n"
