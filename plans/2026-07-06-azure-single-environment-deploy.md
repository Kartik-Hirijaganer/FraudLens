# Deploy FraudLens to Azure — Single Environment (Terraform + GitHub Actions + Infisical)

## Context

**Why this plan exists.** The FraudLens repo already ships a complete, CI-validated but **inert**
Azure deployment stack: parametric Terraform modules (`infra/terraform/`), a build-once-promote-many
backend deploy workflow (`.github/workflows/deploy-backend.yml`), a Vercel frontend deploy
(`deploy-frontend.yml`), a weekly ML base-image build (`build-base.yml`), and Infisical-OIDC secret
fetching. Nothing runs because the Azure/Supabase/Vercel accounts and the Terraform remote-state
backend do not exist yet, and the deploy gates (`AZURE_DEPLOY_ENABLED`, `VERCEL_DEPLOY_ENABLED`) are
off (Golden Rule 1: no `apply`/push until then).

**So this is a go-live activation runbook, not a from-scratch build.** The only repo change is a small
consolidation refactor to honor the "one environment" mandate; everything else is provisioning real
cloud resources, wiring OIDC + Infisical, and flipping the gates.

**Decisions (confirmed with the user):**
- **One Terraform environment** — keep `prod` as the single env; **remove** the `dev` root module.
- **Frontend → Vercel** (keep the scaffolded `deploy-frontend.yml` path; backend-in-Azure hybrid).
- **Database → Supabase** (external managed Postgres; only `DATABASE_URL` lives in Infisical).
- **Registry → public GHCR** (`acr_enabled = false`, the default; no ACR, no registry credential).

**Intended outcome.** A single live Azure environment serving the FastAPI backend on Azure Container
Apps (external HTTPS gateway) + retrain/batch Container Apps Jobs + Blob storage + Log Analytics/App
Insights, fronted by a Vercel-hosted React SPA, backed by Supabase Postgres, with all secrets fetched
at runtime from Infisical `prod`, deployed continuously by GitHub Actions via GitHub→Azure OIDC.

**Governance held throughout:** no PHI, tenant isolation, fail-closed authZ, no secrets in git/state,
single Infisical env `prod`, no commit/push without explicit permission.

---

## Topology (target end state)

```
Vercel (React SPA, VITE_API_BASE_URL -> gateway FQDN)
        │  HTTPS /api/v1/*
        ▼
Azure Container Apps ENV  (fraudlens-prod-rg, single env)
  ├─ gateway_app  (external ingress, HTTPS-only, Multiple-revision, tuned cold-start probes)
  │     ├─ managed identity ─▶ Azure Blob (artifacts, sar-pdfs)      [storage.azure.com token]
  │     ├─ managed identity ─▶ ARM (start Jobs)                       [management.azure.com token]
  │     └─ startup ─▶ Infisical (DATABASE_URL, JWT keys, LLM keys)   [runtime fetch]
  ├─ job_retrain (cron)  + job_batch_score (manual)   — same image, different entrypoint
  └─ ChromaDB RAG index  — BAKED INTO THE IMAGE (no separate service)
        │
        ▼
Supabase Postgres (asyncpg; DATABASE_URL from Infisical)   |   Log Analytics + App Insights
```

State: Supabase Postgres · artifacts/SAR-PDFs: Azure Blob · secrets: Infisical `prod` · image: GHCR.

---

## Phase 1 — Consolidate to a single Terraform environment (only repo change)

Honor the one-environment mandate by removing the `dev` root module and pointing every consumer at the
single `prod` env. Modules under `infra/terraform/modules/` are shared and unchanged.

**Changes:**
- **Delete** `infra/terraform/environments/dev/` (all of `providers.tf`, `variables.tf`, `main.tf`,
  `outputs.tf`, `dev.tfvars`, `backend.tf.template`, `.terraform.lock.hcl`).
- **Makefile** — `tf-validate` target currently loops dev **and** prod; reduce to `prod` only
  (`terraform -chdir=infra/terraform/environments/prod fmt -check / init -backend=false / validate`).
- **`.github/workflows/_ci-reusable.yml`** — the `tf-validate` job: drop the dev invocation, keep prod.
- **`.github/workflows/deploy-backend.yml`** and **`deploy-frontend.yml`** — the `workflow_run` trigger
  lists `branches: [dev, "release/*"]`. With one environment, set the deploy branch to **`main`**
  (single source of truth for go-live). Update the trigger and any branch-specific `if:` guards to
  `main`. (This also aligns the OIDC federated-credential subject in Phase 3.)
- **`docs/runbooks/azure-deploy.md`** + **`infra/terraform/README.md`** — strike the dev/prod dual-env
  language; describe the single `prod` env and the `main`-branch deploy trigger.
- **`tests/integration/test_deploy_flow.py`** — this suite asserts the committed workflow + Terraform
  invariants (dual env, trigger branches). Update its expectations to the single-env / `main`-trigger
  reality so `make ci` stays green.

**Verify:** `make tf-validate` (prod-only, passes with `-backend=false`), `make ci` green
(includes `pytest -k deploy` / `test_deploy_flow.py`), `header-check` + `docs-check` clean.

> Naming note: the single env keeps the `prod` name (matches `config/prod.yaml`, `fraudlens-prod-rg`,
> `fraudlens-prod-api`, the Infisical `prod` env-slug, and `FRAUDLENS_ENVIRONMENT=prod` baked into the
> image) — renaming would ripple through all of those for no benefit. `config/dev.yaml` stays (it is
> the *local* dev overlay for `make local-demo`, not an Azure environment).

---

## Phase 2 — Cloud accounts & prerequisites (out-of-band, manual)

Stand up the accounts the scaffolding assumes. Nothing here touches the repo.

1. **Azure** — subscription under the personal tenant; note `subscription_id` + `tenant_id`. Register
   providers: `Microsoft.App`, `Microsoft.OperationalInsights`, `Microsoft.Insights`,
   `Microsoft.Storage`, `Microsoft.ContainerService` as needed (`az provider register`).
2. **Supabase** — create a project; capture the **pooled** (pgBouncer, port 6543) connection string
   for the app and the **direct** (5432) string for Alembic migrations. Enforce SSL. Do **not** store
   the string anywhere yet (it goes into Infisical in Phase 5).
3. **Vercel** — create the frontend project linked to this repo (root `frontend/`), production only;
   generate a Vercel deploy token (stored in Infisical, not GitHub).
4. **Infisical** — confirm the single `prod` environment exists in the FraudLens project; note the
   `project-slug` (workspace slug). No secrets created yet.
5. **GitHub repo** — create a `production` **Environment** (matches `environment: production` in the
   deploy jobs); optionally add required reviewers as a manual promotion gate.

**Verify:** `az account show` resolves the subscription; Supabase psql connect succeeds from a laptop;
Vercel project visible; Infisical `prod` env present.

---

## Phase 3 — Terraform remote-state backend + GitHub→Azure OIDC

**3a. Bootstrap remote state (out-of-band, one-time)** — per `infra/terraform/README.md §46-57`:
```bash
az group create -n fraudlens-tfstate-rg -l eastus
az storage account create -n fraudlenstfstate -g fraudlens-tfstate-rg -l eastus \
  --sku Standard_LRS --min-tls-version TLS1_2
az storage container create -n tfstate --account-name fraudlenstfstate
```
Then in `infra/terraform/environments/prod/`: rename `backend.tf.template` → `backend.tf` (key
`prod.terraform.tfstate`). This is a repo file rename — do it as part of implementation, committed.

> *Optional IaC-purist alternative (from review):* instead of the `az` commands, add a small
> `infra/terraform/bootstrap/` root (local state, run once) that provisions the state RG + storage +
> container **and** the Entra app / federated credential / role assignments from 3b. Cleaner and
> reproducible, but it is net-new Terraform to author and still has a one-time local-state step. The
> out-of-band `az` path above is the default (zero new code, matches the committed README); pick the
> `bootstrap` root only if you want the OIDC identity itself under version control.

**3b. GitHub→Azure OIDC federated credential** — per `azure-deploy.md §87-96`:
1. Create an Entra app + service principal.
2. Grant it **Contributor** on the subscription **and User Access Administrator** (the `identity`
   module creates role assignments: Blob Data Contributor, and AcrPull only if ACR — here off).
3. Add a **federated credential** trusting this repo's OIDC token — subject
   `repo:Kartik-Hirijaganer/FraudLens:ref:refs/heads/main` **and** the `production` environment
   (add both subject forms if the deploy jobs run under the `production` environment).
4. Capture `client_id`.

**3c. Set GitHub repo *variables*** (not secrets — all non-secret ids):
`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`. Leave `AZURE_DEPLOY_ENABLED` **unset**
(deploy stays inert until Phase 6).

**Verify:** `terraform -chdir=infra/terraform/environments/prod init` succeeds against the azurerm
backend; a throwaway `azure/login@v2` step in a scratch workflow (or `az login --federated-token`)
authenticates with no stored secret.

---

## Phase 4 — Infisical machine identity + runtime secret access

**4a. GitHub Actions → Infisical (OIDC machine identity).** The `migrate` job in `deploy-backend.yml`
and the `deploy` job in `deploy-frontend.yml` fetch secrets via `Infisical/secrets-action@v1.0.16`
(`method: oidc`, `oidc-audience: https://github.com/Kartik-Hirijaganer`, `domain: https://app.infisical.com`).
- Create an Infisical **machine identity** with an **OIDC auth** trust for GitHub Actions (audience =
  the org URL above; subject scoped to this repo/`main`).
- Grant it read on secret paths `/backend` and `/ci/vercel` in env `prod`.
- Capture the identity id → GitHub repo variables `INFISICAL_GITHUB_ACTIONS_IDENTITY_ID` and
  `INFISICAL_PROJECT_SLUG`.

**4b. App runtime → Infisical.** The gateway/jobs fetch secrets at container startup. Confirm the
runtime auth method the app uses (managed-identity/OIDC vs. a bootstrap Infisical token) in
`backend/src/fraudlens_backend/` settings + Infisical client, and provision the matching Infisical
machine identity so `/readyz`'s Infisical check passes. Document it in
`docs/runbooks/infisical-secrets.md`.
- **Typed Infisical settings + real readiness (from review):** ensure the app's `pydantic-settings`
  config exposes typed Infisical fields (Rule 1) and that **`/readyz` reports Infisical `ok`/`down`
  in prod — not `skipped`** — so the smoke gate genuinely proves secret access before promotion. If it
  currently short-circuits, wiring real readiness is a small backend change to fold into Phase 4.

**4c. Populate secrets in Infisical `prod`** (never in git/config/state):
- `/backend`: `DATABASE_URL` (Supabase; the migrate job needs a **non-pooled/direct** URL — see 5b),
  JWT signing key / JWKS config, and (since `config/prod.yaml` sets `llm_mode: live`) the LLM provider
  key(s) — Anthropic and/or OpenAI per the `fraudlens-llm` catalog.
- `/ci/vercel`: `VERCEL_TOKEN`.

**Verify:** an Infisical CLI/API read of `/backend` under `prod` returns the keys; a dry-run of the
`secrets-action` in a scratch workflow masks and injects them.

---

## Phase 5 — Supabase database wiring

1. **Connection strings** — store `DATABASE_URL` in Infisical `/backend`. The app uses the **pooled**
   (6543/pgBouncer) URL; Alembic migrations need a **direct** (5432) session-mode URL (pgBouncer
   transaction mode breaks some DDL). If they differ, add a second key (e.g. `DATABASE_URL_DIRECT`)
   and have the `migrate` job use it — otherwise reuse the direct URL for both.
2. **SSL / network** — Supabase requires TLS; confirm the asyncpg engine and Alembic (`alembic/env.py`)
   pass `sslmode=require`/ssl context. Supabase is publicly reachable, so no VNet peering is needed for
   v1 (Container Apps egress → Supabase over TLS).
3. **Schema** — migrations `alembic/versions/0001_initial_schema.py` + `0002_extend_alert_statuses.py`
   are applied by the deploy `migrate` job (`alembic upgrade head`), not manually.

**Verify:** `uv run alembic upgrade head` against the Supabase direct URL from a laptop creates the
schema; `SELECT 1` over the pooled URL succeeds (this is what `/readyz` runs).

---

## Phase 6 — First Terraform apply (stand up the environment)

Provision the single `prod` environment. `prod.tfvars` already carries the non-secret knobs
(`name_prefix=fraudlens-prod`, `acr_enabled=false`, `storage_account_name=fraudlensprodsa`,
`min_replicas`, `max_replicas=5`, `blob_lifecycle_days=365`, `services_split_enabled=false`,
`retrain_cron`). **Confirm `storage_account_name` is globally unique** before applying.

```bash
cd infra/terraform/environments/prod
terraform init                                   # azurerm backend (Phase 3a)
terraform plan  -var-file=prod.tfvars \
  -var="container_image=mcr.microsoft.com/k8se/quickstart:latest"   # placeholder image for first apply
terraform apply -var-file=prod.tfvars -var="container_image=<placeholder>"
```
This creates: resource group `fraudlens-prod-rg`, VNet + Container-Apps subnet, Log Analytics + App
Insights, Blob storage (`artifacts`, `sar-pdfs` + lifecycle), user-assigned managed identity (Blob
Data Contributor), the external `gateway_app` (`fraudlens-prod-api`), and the retrain + batch-score
Jobs. `service_app` stays at zero instances (`services_split_enabled=false`).

**Capture outputs:** `app_fqdn` (the gateway HTTPS URL — feeds `VITE_API_BASE_URL` in Phase 8),
`identity_client_id`, `resource_group`, `startup_probe_budget_seconds`.

**First-apply notes (chicken-and-egg):** the real backend image does not exist in GHCR until Phase 7,
so the first `apply` uses the **placeholder quickstart image** (`container_image` already defaults to
it — zero code). The gateway boots on the placeholder; the real SHA image lands via the Phase 7
pipeline `stage` step. *(Alternative from review: a `workloads_enabled=false` flag that skips the
gateway/jobs on the first "infra-only" apply — cleaner separation but a net-new tf var; the
placeholder-image approach needs no code and is the default.)* `min_replicas=0` in `prod.tfvars`
(scale-to-zero, see cost section) keeps idle cost ≈$0. `terraform apply` here is run **manually** for
the initial stand-up; thereafter the pipeline's `infra` job re-plans and applies only on drift.

**Verify:** `terraform output` shows the FQDN; `az containerapp show -n fraudlens-prod-api -g
fraudlens-prod-rg` is Running; state object exists in the `tfstate` container.

---

## Phase 7 — First backend build & deploy (activate the pipeline)

1. **Prime the ML base image** — run `build-base.yml` (manual dispatch) so
   `ghcr.io/kartik-hirijaganer/fraudlens-base:latest` exists (the app Dockerfile builds `FROM` it).
   Ensure GHCR packages are readable (public) so Container Apps can pull anonymously (`acr_enabled=false`).
2. **Flip the gate** — set GitHub repo variable `AZURE_DEPLOY_ENABLED=true`.
3. **Trigger deploy** — push/merge to `main`; `ci` runs, then `deploy-backend.yml` fires via
   `workflow_run`. It executes the wired path with **no changes needed** to the workflow logic:
   `verify → build-push (SHA image → GHCR) → infra (plan; apply only on drift) → stage (revision @0%)
   → migrate (Infisical DATABASE_URL → alembic upgrade head) → smoke (/healthz, /readyz, pytest -m
   smoke on the staged FQDN) → promote (100% traffic) | abort (previous revision stays 100%)`.

**Verify:** watch the Actions run; after `promote`, `curl https://<app_fqdn>/healthz` and `/readyz`
return 200 (`/readyz` exercises DB + ChromaDB + active model + Infisical); `az containerapp revision
list` shows the new SHA revision at 100%.

---

## Phase 8 — Frontend (Vercel) activation + CORS closure

Chicken-and-egg between the gateway FQDN and the frontend origin — resolve in this order:

1. Set GitHub repo variable `VITE_API_BASE_URL = https://<app_fqdn>` (from Phase 6, must be HTTPS) and
   `FRONTEND_URL` = the intended Vercel production domain.
2. Ensure Vercel token is in Infisical `/ci/vercel` (Phase 4c). Flip `VERCEL_DEPLOY_ENABLED=true`.
3. Trigger `deploy-frontend.yml` (via `main` CI success): it fetches the Vercel token from Infisical,
   `vercel pull/build --prod` with `VITE_API_BASE_URL` baked in, `vercel deploy --prebuilt --prod`,
   then smoke-tests `FRONTEND_URL` for HTTP 200.
4. **Close CORS** — add the Vercel production domain to `gateway_cors_origins` in `prod.tfvars`
   (currently `[]` = deny-all; `config/prod.yaml` `cors_allow_credentials: true`). Re-run the pipeline
   (or `terraform apply`) so the gateway's `FRAUDLENS_CORS_ALLOW_ORIGINS` env updates and a new
   revision rolls. Keep the list to the exact Vercel origin(s) — no wildcards.

**Verify:** load the Vercel URL; the SPA calls `https://<app_fqdn>/api/v1/*` with no CORS errors
(browser devtools/network); an authenticated flow round-trips.

---

## Phase 9 — Jobs, observability, rollback rehearsal

1. **Retrain cron** — confirm `job_retrain` is scheduled (`retrain_cron` in `prod.tfvars`); trigger a
   manual run (`az containerapp job start -n fraudlens-prod-retrain -g fraudlens-prod-rg`) and confirm
   it reads/writes Blob artifacts via managed identity and honors the gated-eligibility exit codes in
   `scripts/retrain.py`.
2. **Batch-score** — trigger `job_batch_score` on-demand; confirm `fraudlens_backend.jobs.runner` runs.
3. **Observability + cost guardrails (from review)** — confirm Container Apps logs land in Log
   Analytics and (if App Insights enabled) `APPLICATIONINSIGHTS_CONNECTION_STRING` is injected and
   traces appear. Then add the cost/health guardrails:
   - **Azure Budget alert** at a low threshold (e.g. **$10/month**) on `fraudlens-prod-rg`, emailing on
     50/90/100% — the safety net against silent cost drift.
   - **Log Analytics daily cap** (e.g. 0.5–1 GB/day) + 30-day retention to keep ingestion under the
     free 5 GB/mo grant.
   - Alerts on **`/readyz` failure** (availability) and **repeated Container Apps revision failures**.
4. **Rollback rehearsal** — per `docs/runbooks/deploy-rollback.md`: shift traffic back to the prior
   revision (`az containerapp ingress traffic set`) and confirm seconds-scale recovery; rehearse the
   model-registry pointer rollback and a Vercel rollback.

**Verify:** a forced smoke failure on a bad revision auto-aborts (previous revision keeps 100%); manual
traffic-shift-back works; jobs complete with expected exit codes.

---

## Phase 10 — Drift-check, docs, and close-out

1. Run `drift-check plans/2026-07-06-azure-single-environment-deploy.md all` — validate real repo state
   (single env, `main` trigger, gates, OIDC, Infisical paths) against this plan and the governance
   rules (no secrets in git/state, tenant isolation, fail-closed authZ, single Infisical `prod`).
2. `make docs` to refresh the architecture doc's autogen regions + runbook cross-links; `make pre-pr`.
3. Confirm the go-live checklist in `azure-deploy.md §109-115` is fully satisfied and the runbook no
   longer says "INERT".

---

## Monthly cost estimate (personal / cheap-by-default)

Target: **~$1–5/month** for a low-traffic personal deployment, leaning on free tiers and open-source
platforms. Everything off the Azure compute path is $0 on free/hobby tiers.

| Component | Tier / setting | Est. monthly | Notes |
|---|---|---|---|
| **Azure Container Apps — gateway** | Consumption, `min_replicas=0` | **$0–4** | Consumption plan has **no base fee** + a monthly free grant (180k vCPU-s, 360k GiB-s, 2M requests). Scale-to-zero → idle cost ≈ $0; low traffic usually stays inside the free grant. |
| **Container Apps Jobs** (retrain weekly + batch on-demand) | Consumption | **~$0** | A few short runs/month; typically inside the same free grant. |
| **Container Apps environment** | Consumption-only, VNet-injected | **$0** | No hourly environment fee on Consumption; **no NAT gateway** provisioned (would add ~$32/mo — the `networking` module is VNet+subnet only, avoid adding one). |
| **Azure Blob Storage** | Standard LRS + cool-tier lifecycle | **~$0.10–1** | Tiny data (model artifacts + SAR PDFs); lifecycle already tiers to cool + expires SAR PDFs. |
| **Log Analytics + App Insights** | 30-day retention | **~$0** | First **5 GB/mo ingestion free**; a personal workload stays under it. *Cheap lever:* leave App Insights off unless you need tracing. |
| **Terraform state storage** | Standard LRS, one small blob | **~$0.05** | Negligible. |
| **Azure Container Registry** | **Disabled** (`acr_enabled=false`) | **$0** | Using public GHCR instead of ACR Basic (~$5/mo saved). |
| **Supabase** (Postgres) | **Free tier** | **$0** | 500 MB DB, open-source platform. Caveat: free projects **pause after ~1 week idle** (resume on demand) — acceptable for personal use; Pro is $25/mo only if you need always-on. |
| **Vercel** (frontend) | **Hobby** | **$0** | Free for personal, non-commercial. |
| **Infisical** (secrets) | Free tier / self-host | **$0** | Open-source; Cloud free tier or self-host. |
| **GHCR** (images) | Public | **$0** | Free for public packages. |
| **LLM API** (SAR drafting) | pay-per-use | **$0–a few $** | `config/prod.yaml` sets `llm_mode: live`. *Cheap levers:* keep Anthropic **Haiku** (cheapest) and/or set `llm_mode: mock` for $0 until you actually need live drafting. |
| **Estimated total** | | **≈ $1–5/mo** | Dominated by Container Apps traffic beyond the free grant + optional LLM usage. |

**Cost levers baked into the plan (all open-source / free-tier first):**
- **Scale-to-zero** the gateway (`min_replicas=0` in `prod.tfvars`) — accept the ~75s ML cold start
  (already probe-tuned) in exchange for ≈$0 idle cost. Set `min_replicas=1` **only** if cold starts
  become unacceptable (that flips compute to ~$30–40/mo always-on).
- **GHCR over ACR** (`acr_enabled=false`) — chosen; saves the ACR Basic fee.
- **No NAT gateway** — do not add one to the `networking` module (single biggest silent Azure cost).
- **Supabase free + Vercel Hobby + Infisical free/self-host** — the entire data/frontend/secrets tier
  is $0 on open-source platforms.
- **App Insights optional** and a **Log Analytics daily cap (0.5–1 GB/day) + 30-day retention** keep
  observability under the free 5 GB/mo grant.
- **`llm_mode: mock`** (or Haiku-only) keeps AI spend at/near $0 until live drafting is needed.
- **Azure Budget alert (~$10/mo)** on the resource group as a safety net against cost drift (Phase 9).

> **GHCR vs. ACR (vs. the reviewed plan):** a teammate's draft assumed **private ACR (~$5/mo fixed)**.
> Per the user's decision we keep **public GHCR ($0)** — it removes the single largest fixed Azure cost
> and is already fully wired (`acr_enabled=false`). This is the main reason this estimate ($1–5) sits
> below the reviewed plan's $5–10. Flip `acr_enabled=true` later only if you want a private registry.

> Excluded: one-time/negligible costs (egress data transfer at personal scale is pennies) and the
> Azure **free trial credit** ($200 for 30 days) which can cover the first month entirely.

## Files touched (Phase 1 + repo renames only; everything else is cloud config)

| File / path | Change |
|---|---|
| `infra/terraform/environments/dev/` | **Delete** (single-env consolidation) |
| `infra/terraform/environments/prod/backend.tf.template` | Rename → `backend.tf` (Phase 3a) |
| `infra/terraform/environments/prod/prod.tfvars` | Add Vercel origin to `gateway_cors_origins`; confirm unique `storage_account_name` |
| `Makefile` (`tf-validate`) | prod-only |
| `.github/workflows/_ci-reusable.yml` (`tf-validate` job) | prod-only |
| `.github/workflows/deploy-backend.yml` / `deploy-frontend.yml` | trigger branch `dev`/`release/*` → `main` |
| `tests/integration/test_deploy_flow.py` | update invariants to single-env + `main` trigger |
| `docs/runbooks/azure-deploy.md`, `infra/terraform/README.md`, `docs/runbooks/infisical-secrets.md` | de-dual-env; document runtime Infisical auth; drop "INERT" |

**Not modified (already correct):** all `infra/terraform/modules/*`, `backend/Dockerfile`(+`.base`),
`build-base.yml`, `release.yml`, `config/prod.yaml`, `alembic/`. The deploy workflow *logic* is reused
as-is — we only change its trigger branch and flip the enable gates.

## Cloud-side settings summary (no repo secrets)

- **GitHub repo *variables*:** `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`,
  `AZURE_DEPLOY_ENABLED=true`, `VERCEL_DEPLOY_ENABLED=true`, `VITE_API_BASE_URL`, `FRONTEND_URL`,
  `INFISICAL_PROJECT_SLUG`, `INFISICAL_GITHUB_ACTIONS_IDENTITY_ID`.
- **Infisical `prod`:** `/backend` → `DATABASE_URL` (+ direct URL for migrations), JWT keys, LLM key(s);
  `/ci/vercel` → `VERCEL_TOKEN`.
- **Never in git/state:** any of the above secret values (Golden Rule 2; enforced by gitleaks +
  `scripts/check_no_secrets.py`).

## End-to-end verification (post-approval)

1. `make tf-validate && make ci` green after Phase 1 (single-env, no regressions).
2. `terraform apply` (Phase 6) → `terraform output app_fqdn` resolves; RG + Container App Running.
3. Backend go-live (Phase 7): `curl https://<app_fqdn>/healthz` + `/readyz` → 200; new SHA revision @100%.
4. Frontend go-live (Phase 8): Vercel URL loads; `/api/v1/*` round-trips with no CORS errors.
5. Jobs (Phase 9): manual retrain + batch-score complete with expected exit codes; logs in Log Analytics.
6. Resilience: forced-bad revision auto-aborts; manual traffic-shift-back recovers in seconds.
7. `drift-check ... all` clean; `make pre-pr` green.
