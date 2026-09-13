# Runbook — Azure Deploy (fast & reliable)

> **Status: wired, validated, INERT.** The Azure / Vercel / Supabase accounts do not exist yet, so
> the Terraform is `fmt`/`validate`-checked in CI but **never applied**, and the deploy workflows
> only run their cloud jobs when the repo variables `AZURE_DEPLOY_ENABLED` / `VERCEL_DEPLOY_ENABLED`
> are `'true'`. This runbook is the procedure for once the accounts and the Terraform state backend
> exist (Golden Rule 1: no apply/push until then). Rollback lives in
> [`deploy-rollback.md`](deploy-rollback.md).

Implements plan §15 (Terraform & Azure Deployment) and §15.7 (fast & reliable deploy, ADR-013).

## 1. Topology (v1 — single external gateway app)

The gateway edge and the in-process service modules ship as **one Container App** with
`ingress: external` and `allow_insecure_connections = false`. The internal `service_app` split
(`ingress: internal`) is **scaffolded + validated but not applied** (`services_split_enabled =
false`); flipping it true later deploys one internal app per service in the *same* environment with
no code rewrite (ADR-004). Container Apps Jobs run the **retrain cron** + the **on-demand
batch-score** job. State is Supabase Postgres; artifacts/SAR-PDFs are Azure Blob; the ChromaDB index
is **baked into the image**; observability flows to Log Analytics + Application Insights.

The gateway runtime uses managed identity for both operational backends:

- **Azure Blob artifacts/SAR PDFs:** `FRAUDLENS_AZURE_STORAGE_ACCOUNT_NAME`,
  `FRAUDLENS_AZURE_STORAGE_CONTAINER_NAME`, and
  `FRAUDLENS_AZURE_STORAGE_SAR_PDF_CONTAINER_NAME` are injected by Terraform; the app derives the
  Blob endpoint and requests a `https://storage.azure.com/` token from managed identity.
- **Container Apps Jobs:** `FRAUDLENS_AZURE_SUBSCRIPTION_ID`,
  `FRAUDLENS_AZURE_RESOURCE_GROUP_NAME`,
  `FRAUDLENS_AZURE_CONTAINER_APPS_RETRAIN_JOB_NAME`, and
  `FRAUDLENS_AZURE_CONTAINER_APPS_BATCH_SCORE_JOB_NAME` are injected by Terraform; the app starts
  jobs through ARM with a `https://management.azure.com/` token.

The same managed-identity client id and Blob settings are also injected into the Job containers,
so retrain/batch work can read/write artifacts without stored credentials.

```mermaid
flowchart LR
    CI["CI: make ci + build ONE image (SHA)<br/>FROM fraudlens-base + cached layers"] --> PUSH["push thin image to GHCR"]
    PUSH --> INFRA{infra changed?}
    INFRA -->|yes| TF["terraform plan + apply (locked)"]
    INFRA -->|no| REV
    TF --> REV["new Container Apps revision @ 0% traffic"]
    REV --> MIG["migrations: expand/contract, gated, timeout+retry"]
    MIG --> SMOKE["smoke: /healthz + /readyz + pytest -m smoke"]
    SMOKE -->|green| PROMOTE["shift traffic to 100% (zero-downtime)"]
    SMOKE -->|fail| ABORT["auto-abort: previous revision stays @ 100%"]
    PROMOTE --> WATCH["post-deploy alerts"]
    WATCH -.regression.-> ROLLBACK["traffic-shift back + model-pointer rollback (seconds)"]
```

## 2. Fast & reliable deploy (what the workflow does)

| Property | How |
|---|---|
| **Build-once, promote-many** | [`deploy-backend.yml`](../../.github/workflows/deploy-backend.yml) builds **one** image tagged by commit SHA, pushes it to GHCR, and reuses that exact ref through stage → promote (never rebuilt). |
| **Thin, cached builds** | The app image is `FROM fraudlens-base` (the weekly [`build-base.yml`](../../.github/workflows/build-base.yml) prebuilds the xgboost/shap/chromadb/langchain layer); BuildKit + GHCR registry cache + GHA cache mean only changed layers rebuild. |
| **App rollout ≠ infra** | `terraform apply` runs **only when `plan -detailed-exitcode` reports changes**; the common code deploy is a fast `az containerapp update` (a new revision) — seconds, no terraform. |
| **Revision @0% → promote-or-abort** | The new revision is staged at 0% traffic (`--revision-suffix`, Multiple revision mode); gated migration → smoke → promote to 100% only if green, else **auto-abort** (the previous revision keeps serving 100%). |
| **Gated expand/contract migrations** | Alembic `upgrade head` runs as a pre-promote step (own timeout + retry). Migrations are backward-compatible, so old + new revisions coexist during the shift and a failed migration blocks promotion without breaking the live revision. |
| **Cold-start-safe probes** | The gateway `startup_probe` budget (`failure_count_threshold × interval`) **exceeds the ≤75s cold start** so the platform never kills a still-loading ML container; liveness engages only after startup; `/readyz` (DB + ChromaDB + active model + Infisical) gates traffic. |
| **Resilience** | Per-job timeouts, step retries with backoff, `concurrency` that never cancels an in-flight deploy. |

The `tests/integration/test_deploy_flow.py` suite (`pytest -k deploy`) asserts these invariants
against the committed workflow + Terraform files.

## 3. Prebuilt ML base image

`build-base.yml` (weekly + on-demand) builds [`backend/Dockerfile.base`](../../backend/Dockerfile.base)
— python + uv + the heavy ML dependency closure — and pushes `ghcr.io/<owner>/fraudlens-base`.
`backend/Dockerfile` defaults `BASE_IMAGE` to `python:3.11-slim-bookworm` (so `make docker-build`
is self-contained), and the deploy build overrides it to the prebuilt base for seconds-scale builds.

## 4. State backend bootstrap (one-time, out-of-band) — ✅ DONE 2026-09-13

State storage exists: RG `fraudlens-tfstate-rg` (eastus), storage account `fraudlenstfstate`
(Standard_LRS, TLS1_2 min, public blob access **disabled**, versioning **on**), container
`tfstate` with keys `dev.terraform.tfstate` / `prod.terraform.tfstate`.

```bash
az group create -n fraudlens-tfstate-rg -l eastus
az storage account create -n fraudlenstfstate -g fraudlens-tfstate-rg -l eastus \
  --sku Standard_LRS --min-tls-version TLS1_2 --allow-blob-public-access false --kind StorageV2
az storage account blob-service-properties update -n fraudlenstfstate \
  -g fraudlens-tfstate-rg --enable-versioning true
az storage container create -n tfstate --account-name fraudlenstfstate
```

`backend.tf` is **generated, never renamed** — `backend.tf.template` stays committed, the
deploy job does `cp backend.tf.template backend.tf`, and the generated file is gitignored.
Locally: `cp backend.tf.template backend.tf && terraform init`.

## 5. GitHub → Azure OIDC (no stored secrets) — ✅ DONE 2026-09-13

The pipeline authenticates with a **federated credential** — no client secret in GitHub.
Entra app `fraudlens-github-oidc` holds `Contributor` + `User Access Administrator` at
subscription scope (the latter is needed because the `identity` module creates the
`acr_pull` / `blob_contributor` role assignments).

Registered federated subjects:

| Subject | Why |
|---|---|
| `repo:Kartik-Hirijaganer/FraudLens:environment:production` | all Azure jobs run under `environment: production` |
| `repo:Kartik-Hirijaganer/FraudLens:environment:Production` | the stored GitHub environment is capital-P; the `sub` claim must match exactly |
| `repo:Kartik-Hirijaganer/FraudLens:ref:refs/heads/dev` | deploy triggers on `dev` |
| `repo:Kartik-Hirijaganer/FraudLens:ref:refs/heads/main` | headroom for main-triggered jobs |

> Adding a subject in **zsh**: always brace the variable (`repo:${REPO}:environment:…`).
> Bare `$REPO:e` / `$REPO:r` are zsh *parameter modifiers* and will silently eat the `:e`
> of `:environment` and the `:r` of `:ref`, producing a malformed subject that fails auth
> at runtime with no obvious cause.

Workflows set `permissions: id-token: write`, use `azure/login@v2`, and Terraform's
`provider "azurerm" { use_oidc = true }` — no secret needed.

**Account ids** (subscription/tenant/client) are non-secret and supplied via `TF_VAR_*` /
`azure/login`. **App + DB secrets** (DATABASE_URL, JWT keys, provider keys) are fetched at runtime
from **Infisical** by the app/Jobs — never Terraform inputs, never baked into the image. See
[`infisical-secrets.md`](infisical-secrets.md).

## 6. Image source — GHCR (default) vs ACR

`acr_enabled = false` (default) → the gateway pulls a **public GHCR** image anonymously (free, no
registry credential). Set `acr_enabled = true` in the env tfvars to provision ACR and pull via the
user-assigned identity's `AcrPull` role instead.

## 7. Enabling deploy (one time)

Steps 1–2 are **done** (2026-09-13). Deploy is intentionally still **off**.

| # | Step | Status |
|---|---|---|
| 1 | Azure account + state backend (§4) | ✅ done |
| 2 | OIDC federation (§5) | ✅ done |
| 2b | Repo **variables** `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` | ✅ set |
| 2c | `FRONTEND_URL`, `INFISICAL_PROJECT_SLUG`, `INFISICAL_GITHUB_ACTIONS_IDENTITY_ID` | ✅ pre-existing |
| 2d | `VITE_API_BASE_URL` (HTTPS gateway URL) | ⬜ not knowable until the first apply produces `app_fqdn` |
| 3 | Supabase + Vercel provisioning | ⬜ separate from Azure |
| 4 | Flip `AZURE_DEPLOY_ENABLED=true` / `VERCEL_DEPLOY_ENABLED=true` | ⬜ **the only switch left** |

```bash
gh variable set AZURE_DEPLOY_ENABLED --body true --repo Kartik-Hirijaganer/FraudLens
```

Until that flip, every Azure job in `deploy-backend.yml` / `deploy-frontend.yml` is gated by
`if: ${{ vars.AZURE_DEPLOY_ENABLED == 'true' }}` and skips — so nothing runs and nothing bills.
Local development is unaffected either way.

After the first successful apply, read `app_fqdn` from the Terraform outputs and set
`VITE_API_BASE_URL` (step 2d) so the frontend targets the gateway.

## 8. Documented switch paths (off by default)

- **Internal service split (ADR-004):** set `services_split_enabled = true` to deploy
  internal-ingress `service_app`s behind the gateway; add APIM Consumption in front for managed
  policies/portal/keys.
- **Azure Database for PostgreSQL (ADR-011):** Supabase is the default; the all-in-Azure Burstable
  alternative is documented as a switch path (not built in v1).
- **Azure Key Vault (ADR-010):** intentionally **not** the app secret store — secrets stay in
  Infisical. No Key Vault module is included by governance.
- **Azure OpenAI (ADR-003):** the compliance-upgrade LLM path, selectable via the LLM catalog config
  with no code change when real PHI is in scope.

## 9. Verification & rollback

- **Smoke** hits the **staged** revision's `/healthz` + `/readyz` and runs `pytest -m smoke` before
  any traffic shift; promotion is conditional on green smoke.
- **Rollback** is a traffic shift back to the prior revision (seconds) + model-registry pointer
  rollback (no redeploy) + Vercel rollback — see [`deploy-rollback.md`](deploy-rollback.md).
