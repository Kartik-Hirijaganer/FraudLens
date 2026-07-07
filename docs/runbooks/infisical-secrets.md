# Runbook — Infisical Secrets

> Status: active. The repo is wired to treat Infisical Cloud
> (`https://app.infisical.com`) as the secrets source of truth. FraudLens uses a single
> Infisical environment, `prod`, for both local agent workflows and deploy automation.

## What

Use Infisical for all FraudLens secrets:

| Surface | Auth method | Secret delivery |
| --- | --- | --- |
| Local development | User login via `infisical login` | `infisical run --env=prod --path=<path> -- <command>` |
| GitHub Actions deploy jobs | Infisical machine identity + GitHub OIDC | `Infisical/secrets-action` injects env vars at job runtime |
| Azure Container Apps runtime | Infisical machine identity + Azure Auth | Infisical SDK/agent fetches secrets using Azure managed identity |

AWS SSM and Azure Key Vault are not FraudLens app secret stores. Azure OIDC remains the
cloud deploy authentication mechanism; it is separate from application secret storage.

## Why

- Keeps secrets out of git, `.env`, Docker images, Terraform state, and GitHub Secrets.
- Uses short-lived workload identity instead of long-lived deploy credentials.
- Works for a personal project without requiring Akeyless enterprise features.
- Preserves FraudLens controls: least privilege, auditability, and no PHI/secrets in logs.

## Infisical Project

Create one Infisical project:

| Setting | Value |
| --- | --- |
| Project name | `FraudLens` |
| Suggested project slug | `fraudlens` |
| Environments | `prod` only |
| Cloud URL | `https://app.infisical.com` |

Use these paths:

| Path | Environment | Purpose | Initial secrets |
| --- | --- | --- | --- |
| `/backend` | `prod` | Backend runtime secrets | `DATABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` |
| `/ci/vercel` | `prod` | Frontend deploy job | `VERCEL_TOKEN` |
| `/ci/supabase` | `prod` | Future Supabase automation | Add only when a workflow consumes it |
| `/mcp/context7` | `prod` | Local agent/CLI workflows that need Context7 docs access | `CONTEXT7_API_KEY` |
| `/mcp/statsig` | `prod` | Local agent/CLI workflows that need Statsig credentials | Add only if a local Statsig API key is required outside the installed connector |
| `/llm` | `prod` | LLM provider credentials | `OPENROUTER_API_KEY` |

Do not store frontend runtime secrets. Any `VITE_*` value bundled into the SPA is public.

## Local Setup

Install and initialize the CLI from the repo root:

```bash
brew install infisical/get-cli/infisical
infisical login
infisical init
```

`infisical init` creates `.infisical.json`. Infisical documents this file as non-secret;
commit it only after confirming it contains project metadata and no credentials.

Run local processes with injected secrets:

```bash
infisical run --env=prod --path=/backend -- \
  uv run uvicorn fraudlens_backend.main:app --reload

infisical run --env=prod --path=/frontend -- \
  npm --prefix frontend run dev

infisical run --env=prod --path=/mcp/context7 -- \
  <context7-related-command>

infisical run --env=prod --path=/mcp/statsig -- \
  <statsig-related-command>

infisical run --env=prod --path=/llm -- \
  uv run python -c "import asyncio, fraudlens_llm as f; c=f.LlmClient.from_settings(); print(asyncio.run(c.generate(messages=[f.LlmMessage(role='user', content='ping')])).safe_text)"
```

If a command does not need secrets, run it normally. Keep `.env` non-secret/local-only.

## LLM Provider Secrets

`fraudlens-llm` reads provider keys lazily from env-var references in
`config/llm/providers.yml`. Track B's live SAR path uses OpenRouter only; store the actual
value only in Infisical `prod` at `/llm`:

| Key | Used by provider |
| --- | --- |
| `OPENROUTER_API_KEY` | `openrouter` |

Run LLM commands through `infisical run --env=prod --path=/llm -- <command>`. Do not copy
provider keys into `.env`, YAML, fixtures, test output, logs, or GitHub Secrets.

## Supabase Runtime Secrets

Store Supabase backend secrets only in Infisical `prod` at `/backend`:

| Key | Purpose |
| --- | --- |
| `DATABASE_URL` | Async SQLAlchemy URL for Supabase Postgres. Use the direct/non-pooled URL for Alembic migrations. |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase Auth Admin API key used only by `POST /api/v1/users`. |

Do not put the service-role key in `frontend/.env.local`, Vercel variables, docs, or source. The
frontend only receives `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY`, which are publishable.

## GitHub Actions OIDC

Create a project or organization machine identity named `github-actions-production`.
Configure it with OIDC Auth:

| Field | Value |
| --- | --- |
| OIDC discovery URL | `https://token.actions.githubusercontent.com` |
| Issuer | `https://token.actions.githubusercontent.com` |
| Subject | `repo:Kartik-Hirijaganer/FraudLens:environment:production` |
| Audience | `https://github.com/Kartik-Hirijaganer` |
| Project access | read-only, `prod`, path `/ci/vercel` |

Copy the identity ID and set these GitHub repository variables:

| Variable | Secret? | Value |
| --- | --- | --- |
| `INFISICAL_PROJECT_SLUG` | No | `fraudlens` or the actual project slug |
| `INFISICAL_GITHUB_ACTIONS_IDENTITY_ID` | No | Machine identity ID from Infisical |
| `FRONTEND_URL` | No | Production frontend URL, once Vercel exists |
| `VERCEL_DEPLOY_ENABLED` | No | `true` only after Vercel and Infisical are ready |

Do not create GitHub Secrets for Infisical or Vercel. Store `VERCEL_TOKEN` only in
Infisical at `prod` → `/ci/vercel` → `VERCEL_TOKEN`.

## Azure Runtime Auth

When the backend needs real runtime secrets:

1. Enable a system-assigned or user-assigned managed identity on the Azure Container App.
2. Create an Infisical identity named `azure-container-app-prod`.
3. Configure Azure Auth with the Azure tenant id, resource/audience
   `https://management.azure.com/`, and the allowed service principal id for the Container
   App managed identity.
4. Add the identity to the FraudLens project with read-only access to `prod` → `/backend`.
5. Wire the backend through the Infisical SDK or Agent so the app reads secrets at runtime.

Do not pass application secrets as Terraform variables. Terraform may receive only
non-secret identifiers such as subscription id, tenant id, client id, project slug, and
identity id.

## Verification

Use these checks after setup:

```bash
infisical run --env=prod --path=/backend -- \
  bash -lc 'test -n "${DATABASE_URL:-}" && echo DATABASE_URL_present'
make secrets-scan
```

Never paste secret values into terminal output, logs, issues, docs, or PR comments.

## References

- Infisical CLI quickstart: https://infisical.com/docs/cli/usage
- Infisical GitHub Actions OIDC: https://infisical.com/docs/integrations/cicd/githubactions
- Infisical Azure Auth: https://infisical.com/docs/documentation/platform/identities/azure-auth
- Infisical machine identities: https://infisical.com/docs/documentation/platform/identities/machine-identities
