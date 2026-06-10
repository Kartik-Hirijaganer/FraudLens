# Configuration

Layered, **non-secret** configuration for FraudLens. Loaded by the backend
`AppSettings` model (`backend/src/fraudlens_backend/settings.py`, built on
`pydantic-settings`).

## Precedence (lowest → highest)

1. `config/default.yaml` — baseline defaults shared by every environment.
2. `config/<environment>.yaml` — the active environment overlay (`dev` or `prod`),
   selected by the `FRAUDLENS_ENVIRONMENT` env var (default `dev`).
3. `FRAUDLENS_*` environment variables — final override (e.g. `FRAUDLENS_LOG_LEVEL=DEBUG`).

A later layer overrides the same key in an earlier layer. `AppSettings` uses
`extra="forbid"`, so an unknown key in YAML or env fails fast rather than being
silently ignored.

## The Akeyless boundary (Golden Rule 2)

These files hold **only non-secret config** — names, log levels, the API prefix,
feature flags. **Secrets never live here.** Credentials (database URLs with
passwords, JWT signing keys, third-party API keys) are fetched **at runtime from
Akeyless** and injected as process environment by the deploy platform, never
written to a YAML file, `.env`, fixture, or source. `gitleaks` scans the whole
repo (including this directory) to enforce that.

## Keys

| Key | Type | Meaning |
|-----|------|---------|
| `app_name` | str | Human-readable service name. |
| `environment` | `dev` \| `prod` | Active environment; selects the overlay and gates the auth dev-bypass. |
| `log_level` | str | Python logging level for the structured logger. |
| `api_v1_prefix` | str | Business-API prefix (`/api/v1`). Ops endpoints stay unprefixed. |
| `request_id_header` | str | Response header carrying the per-request correlation id. |
| `auth_dev_bypass` | bool | Dev-only auth bypass. Honored **only** when `environment != "prod"`; inert in prod. |
