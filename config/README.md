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

## The Infisical boundary (Golden Rule 2)

These files hold **only non-secret config** — names, log levels, the API prefix,
feature flags. **Secrets never live here.** Credentials (database URLs with
passwords, JWT signing keys, third-party API keys) are fetched **at runtime from
Infisical** and injected as process environment by the local command, CI job, or
deploy platform, never written to a YAML file, `.env`, fixture, or source.
`gitleaks` scans the whole repo (including this directory) to enforce that.

## Keys

| Key | Type | Meaning |
|-----|------|---------|
| `app_name` | str | Human-readable service name. |
| `environment` | `dev` \| `prod` | Active environment; selects the overlay and gates the auth dev-bypass. |
| `log_level` | str | Python logging level for the structured logger. |
| `api_v1_prefix` | str | Business-API prefix (`/api/v1`). Ops endpoints stay unprefixed. |
| `request_id_header` | str | Response header carrying the per-request correlation id. |
| `auth_dev_bypass` | bool | Dev-only auth bypass. Honored **only** when `environment != "prod"`; inert in prod. |

## LLM registry files

`config/llm/catalog.yml` and `config/llm/providers.yml` are non-secret registries for
the standalone `fraudlens-llm` package:

| File | Purpose | Secret policy |
|------|---------|---------------|
| `config/llm/catalog.yml` | Capability and trust registry keyed `provider -> model-id`. Contains `kind`, `context_window`, `modality`, `default_params`, pricing, `speed`, `reasoning_capable`, `intelligence`, `source_url`, `verified_at`, `lifecycle`, `callable`, and `pricing_basis`. | No endpoints or API keys. |
| `config/llm/providers.yml` | Connection and governance registry keyed by provider. Contains `protocol`, `base_url`, `api_key_env`, `timeout_s`, `max_retries`, non-secret `headers`, `region`, `data_retention`, `zdr_supported`, `training_opt_out`, `baa_required`, and `allowed_data_classes`. | `api_key_env` is an env-var name only; values come from Infisical `/llm`. |

Model references split on the first slash: `openai/gpt-5-mini` and
`openrouter/anthropic/claude-sonnet-4.6` both resolve cleanly. A provider present in
the catalog but absent from `providers.yml` is discoverable but not callable.

Validate the registries with:

```bash
make llm-catalog-check
```
