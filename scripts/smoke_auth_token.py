"""Summary: Mint short-lived Supabase access tokens for the CONFIGURED portfolio-demo personas so
the production smoke exercises real, role-separated behaviour instead of an unauthenticated subset.
Every identity value is read from `config/portfolio-demo.yaml`; the shared public synthetic password
and the publishable anon key arrive as injected environment. A minted token is masked in the
workflow log the instant it exists and is written only to the job environment file, so it never
reaches stdout, an artifact, a job output, or another job.

Key classes:
- PersonaExport: one persona key mapped to the environment variable its token is exported as.
- SupabaseSignIn: the project endpoint, publishable key, and synthetic password one grant needs.
- SmokeTokenError: safe minting failure that names a persona or a missing input, never a value.

Key functions:
- parse_exports: validate `<persona>=<ENV_NAME>` pairs against the configured personas.
- mint_token: exchange the public synthetic password for one persona's access token.
- export_tokens: mint every requested persona token and emit mask + environment directives.
- main: resolve the injected inputs and export the requested persona tokens.

Notes:
- The password grant is sent as a JSON body, never as a query string or an argv value, so the
  credential cannot reach a process listing or a URL (FraudLens no-secrets-in-URLs rule).
- `::add-mask::` is printed BEFORE the token is written anywhere, so the runner redacts it from
  every later line; the token itself is never printed by this script.
- Roles are NOT asserted here. The server owns the role on the `public.users` row and stamps it
  into the JWT; asserting it locally would be a second source of truth for authorization.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field

from fraudlens_backend.portfolio_demo import PortfolioDemoConfig, load_portfolio_demo_config

_TOKEN_PATH = "/auth/v1/token"
_TIMEOUT_SECONDS = 30.0
_HTTP_OK = 200
_ANON_KEY_ENV = ("VITE_SUPABASE_ANON_KEY", "SUPABASE_ANON_KEY")
_PASSWORD_ENV = "FRAUDLENS_DEMO_AUTH_PASSWORD"
_URL_ENV = "SUPABASE_URL"


class SmokeTokenError(RuntimeError):
    """Raised when an input is missing or Supabase rejects a persona sign-in."""


class PersonaExport(BaseModel):
    """One configured persona and the environment variable its token is exported as."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    persona_key: str = Field(..., min_length=1, description="Persona key in the demo story.")
    email: str = Field(..., min_length=1, description="Configured persona sign-in email.")
    env_name: str = Field(..., min_length=1, description="Environment variable to export into.")


class SupabaseSignIn(BaseModel):
    """Everything one password grant needs, resolved once from the injected environment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str = Field(..., min_length=1, description="Supabase project URL.")
    anon_key: str = Field(..., min_length=1, description="Publishable anon key sent as `apikey`.")
    password: str = Field(..., min_length=1, description="Public synthetic demo password.")

    @property
    def token_url(self) -> str:
        """Return the password-grant endpoint, tolerating a trailing slash on the project URL."""
        return f"{self.base_url.rstrip('/')}{_TOKEN_PATH}"


def parse_exports(pairs: Sequence[str], config: PortfolioDemoConfig) -> tuple[PersonaExport, ...]:
    """Validate `<persona>=<ENV_NAME>` pairs against the configured personas."""
    known = {persona.key: persona for persona in config.personas}
    exports: list[PersonaExport] = []
    for pair in pairs:
        persona_key, separator, env_name = pair.partition("=")
        if not separator or not persona_key or not env_name:
            raise SmokeTokenError(f"export must be <persona>=<ENV_NAME>, got '{pair}'")
        persona = known.get(persona_key)
        if persona is None:
            raise SmokeTokenError(
                f"persona '{persona_key}' is not configured; the story declares {sorted(known)}"
            )
        exports.append(
            PersonaExport(persona_key=persona_key, email=persona.email, env_name=env_name)
        )
    if not exports:
        raise SmokeTokenError("at least one --export <persona>=<ENV_NAME> is required")
    return tuple(exports)


def mint_token(client: httpx.Client, sign_in: SupabaseSignIn, email: str) -> str:
    """Exchange the public synthetic password for one persona's Supabase access token."""
    response = client.post(
        sign_in.token_url,
        params={"grant_type": "password"},
        headers={"apikey": sign_in.anon_key, "Content-Type": "application/json"},
        json={"email": email, "password": sign_in.password},
    )
    if response.status_code != _HTTP_OK:
        raise SmokeTokenError(
            f"Supabase rejected the persona sign-in (HTTP {response.status_code}); "
            "the personas may not be provisioned against this project"
        )
    token = str(response.json().get("access_token", ""))
    if not token:
        raise SmokeTokenError("Supabase returned no access token for the persona sign-in")
    return token


def export_tokens(
    client: httpx.Client,
    exports: Sequence[PersonaExport],
    sign_in: SupabaseSignIn,
    environment_file: Path | None,
) -> None:
    """Mint each persona token, mask it immediately, and append it to the job environment."""
    for export in exports:
        token = mint_token(client, sign_in, export.email)
        # Masked first: every later line that could echo the value is redacted by the runner.
        print(f"::add-mask::{token}")
        if environment_file is not None:
            with environment_file.open("a", encoding="utf-8") as handle:
                handle.write(f"{export.env_name}={token}\n")
        print(f">> minted a short-lived token for '{export.persona_key}' -> {export.env_name}")


def _required_env(names: Sequence[str], *, label: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    raise SmokeTokenError(f"{label} is not injected; expected one of {list(names)}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mint short-lived Supabase tokens for the configured demo personas."
    )
    parser.add_argument(
        "--export",
        action="append",
        default=[],
        metavar="PERSONA=ENV_NAME",
        help="Persona key and the environment variable to export its token as.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Resolve the injected inputs and export a token for every requested persona."""
    args = _build_parser().parse_args(argv)
    try:
        exports = parse_exports(args.export, load_portfolio_demo_config())
        sign_in = SupabaseSignIn(
            base_url=_required_env((_URL_ENV,), label="the Supabase project URL"),
            anon_key=_required_env(_ANON_KEY_ENV, label="the publishable Supabase anon key"),
            password=_required_env((_PASSWORD_ENV,), label="the public synthetic demo password"),
        )
        github_env = os.environ.get("GITHUB_ENV", "").strip()
        with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
            export_tokens(client, exports, sign_in, Path(github_env) if github_env else None)
    except SmokeTokenError as error:
        print(f"smoke auth token failed: {error}")
        return 1
    except httpx.HTTPError:
        print("smoke auth token failed: the Supabase auth endpoint was unreachable")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
