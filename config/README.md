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
| `portfolio_demo_enabled` | bool | Gates the portfolio demo story surface. A security gate, so it also defaults to `False` **in Python** — a missing YAML key leaves it off instead of failing boot. |
| `portfolio_demo_config_file` | str | **Filename** of the story document, resolved under the config directory; absolute paths and upward traversal are rejected by the loader. |

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

## GFP benchmark protocol (`gfp-benchmark.yaml`)

`config/gfp-benchmark.yaml` pins the **offline** GFP tenant-isolation study protocol
(seed, batch size, engine pin, time windows, histogram bins, sampling ladder, fold
fractions, target quotas, `.local/` IO paths). It is **not** loaded by `AppSettings`:
`scripts/lib/gfp/config.py` (`GfpBenchmarkConfig`, frozen + `extra="forbid"`) validates
it and rejects bad windows/bins/fractions/quotas/paths/engine-versions. The values were
frozen with [ADR-017](../docs/architecture/adr/ADR-017-graph-feature-serving-boundary.md)
and never influence live scoring. Non-secret (Golden Rule 2): dataset files and study
outputs stay under gitignored `.local/`.

## Portfolio demo story (`portfolio-demo.yaml`)

`config/portfolio-demo.yaml` is the single source of every **demo-specific** value: the one runtime
demo tenant, its login personas, the pinned scoring model, the authored case pack, and the
distribution a real pipeline run must reproduce. Like `gfp-benchmark.yaml` it is **not** loaded by
`AppSettings`: `backend/src/fraudlens_backend/portfolio_demo/config.py`
(`PortfolioDemoConfig`, frozen + `extra="forbid"`) validates it and rejects unknown keys, unresolved
persona references, roles the RBAC policy does not permit, unknown rule codes, colliding masked
accounts, and any `expected:` block that is not the algebraic consequence of the scenario list. Only
the **filename** comes from `AppSettings` (`portfolio_demo_config_file`), resolved under this
directory with traversal and symlink escapes rejected.

Non-secret (Golden Rule 2): the public synthetic demo password is **not** written here. The document
carries `auth.public_synthetic_password_env` — the env-reference form `check_no_secrets.py`
sanctions — and the value resolves from Infisical `prod`. `scripts/check_no_demo_literals.py` derives
its forbidden literals from this file, so any story value restated in code, docs, tests, or workflows
fails `make demo-literals-check`.

Which values belong here versus in layered app config, workflow YAML, or Infisical — and which edits
force a recalibration — is documented in
[`docs/runbooks/portfolio-demo.md`](../docs/runbooks/portfolio-demo.md); the provenance rationale is
[ADR-018](../docs/architecture/adr/ADR-018-portfolio-demo-data-provenance.md).
