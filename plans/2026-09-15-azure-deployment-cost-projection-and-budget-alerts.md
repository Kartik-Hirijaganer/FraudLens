# Azure Deployment, Cost Projection, and Budget Alerts — Release 0.4.0

> **Status:** approved 2026-09-15; not yet implemented. Audit each phase after implementing with
> `drift-check plans/2026-09-15-azure-deployment-cost-projection-and-budget-alerts.md phase=<N>`
> (or `… all`). Nothing in this plan has been applied — no Azure resource has been created.

---

## Executive summary (plain language)

**The problem.** Your resume says the AML pipeline is "deployed on Azure AKS with Terraform/HPA."
Nothing is deployed. The app runs on localhost. Every Azure deploy job in CI is switched off by a
feature flag, and no Azure compute resource has ever been created for this project.

**The good news.** Release 0.3.0 built almost all of the machinery — a hardened AKS Terraform
module, Kubernetes manifests with a real HPA, a full `make aks-*` command set, a budget module, and
a dispatch-only deploy workflow. It was deliberately validated but never applied. This plan is not
"build Azure from scratch." It is **"execute the apply that 0.3 deferred, fix the defects that
would make it fail or overcharge you, and put real cost controls in front of it."**

**What you get.**

| | Where | Cost | What it proves |
|---|---|---|---|
| **Permanent live URL** | `https://fraud-lens-amber.vercel.app` — frontend on Vercel, `/api/*` proxied to Azure Container Apps | **~$2.50/month** | A recruiter clicks your existing link and the whole app works, instantly |
| **AKS + Terraform + HPA** | Azure AKS, created and destroyed per session | **~$0.79 per 4-hour session, $0 between** | The resume claim, backed by a committed evidence artifact with measured numbers |
| **Cost controls** | Budgets, hard caps, daily watchdog | $0 | You are alerted early, and the app physically cannot scale into a large bill |

**Total: ~$2.50/month, plus ~$0.79 each time you bring AKS up.**

**Five defects I found that would have cost you money or broken the deploy** — each verified
against your live Azure account or the code, not assumed:

1. **The Container Apps environment is wired to a custom VNet** (`prod/main.tf:78`), which makes
   Azure provision a Standard Load Balancer and public IP — **~$22/month of fixed overhead**,
   billed whether anyone visits or not. Your app only talks to public Supabase, OpenRouter, and
   Infisical endpoints, so the VNet buys nothing. Removing it is the single largest saving in this
   plan — nearly 10× the entire remaining monthly cost.
2. **The node size in your AKS Terraform cannot be created.** `Standard_D2as_v5` has a quota of
   **zero** vCPUs in westus3 on your subscription. `terraform apply` fails immediately.
3. **Container Apps would crash on boot.** Terraform emits the CORS setting as a comma-joined
   string; the app parses that field as JSON. It fails to decode and the process dies before
   serving a request.
4. **There is no spend limit on the AI calls.** The app has a 120-requests-per-minute rate limit but
   **no cap on OpenRouter spend**. A public URL running in live-LLM mode has nothing stopping a
   bot from running up a model bill.
5. **Log ingestion is uncapped.** The Log Analytics workspace has no daily quota, so a logging loop
   or a traffic spike bills at $2.30/GB with no ceiling.

**One thing blocks everything else, and it is not an Azure problem.** `make ci` and `make pre-pr`
**fail right now** with 13 dead documentation links: three plan files are staged for deletion while
10 ADRs and 3 rows of `plans/README.md` still link to them. Every phase below ends in `make pre-pr`,
so Phase 0 step 1 repairs this first, following the retirement convention your own
`plans/README.md` already states.

**Your spending limit is off.** This is Pay-As-You-Go with no hard cap — Azure budgets *alert*, they
never stop anything, and their data lags by hours. That is why the plan puts cost controls in
before any spend (Phase 2) and relies on **hard limits that cannot be exceeded** (one replica
maximum, capped log ingestion, a daily LLM ceiling, destroy-after-use) rather than on alerts alone.

**Why AKS is not left running.** A cluster bills for its nodes whether used or not — ~$0.17/hour,
~$126/month, roughly 50× the Container Apps cost for the same visible result. So Terraform creates
it when you want it (~12 min), you use it, and it is destroyed. What persists at $0 is the
Terraform code, the **committed evidence artifact** with measured HPA numbers, and the workflow
logs — which is what you actually show someone. The claim stays literally true: "deployed on Azure
AKS with Terraform/HPA" describes work you did and can reproduce on demand. Be ready to say
"it's ephemeral by design — here's the Terraform and the measured evidence; I can have it live in
twelve minutes."

---

## Context

**Why now.** You are actively applying with a resume that states two things as complete which are
not. You chose to leave the wording as-is and close the gap fast. This plan covers the deployment
half; the AWQ→BF16 quality cascade is separate work.

**Why this is mostly assembly.** `plans/README.md` already lists this as 0.4.0 first-step item 6:
*"Run the human-approved AKS demonstration: apply the validated Terraform, deploy, capture HPA and
durability evidence, then stop or destroy and verify clean."* ADR-021 defines the topology and
scopes the apply to "the next release." This is that release.

**The governance.** Golden Rule 7 requires explicit human permission for every billable or mutating
cloud action. ADR-028 requires a budget allocation, a ledger row, a projection, and a *verified*
teardown for every paid session. ADR-021 forbids claiming "deployed on AKS" from kind evidence.
`.claude/settings.json` enforces this mechanically — every mutating `make aks-*` target refuses
without `CONFIRM=yes`.

**Outcome.** Release 0.4.0 is **published as a GitHub Release** (Phase 7) carrying: a live
recruiter-facing URL, a reproducible AKS session producing
`docs/reference/benchmarks/aks-hpa-scaling.{json,md}`, claims moved from `tested` to
`demonstrated`, budget alerts at two scopes, hard spend caps, and a generated cost model.

**Branch and tag naming.** The working branch is `release/0.4.0` — renamed from the misspelled
`releas/v0.4.0` on 2026-09-15. The repo's convention, set by `release/0.3.0` and `v0.3.0`, is that
**branches omit the `v` and tags carry it**. `deploy-backend.yml` triggers on `release/*` and
`release.yml` on `v*`, so both now match.

---

## Current state (verified live)

### Azure account

| Fact | Value |
|---|---|
| Subscription | `01417138-33d6-4b26-a0e2-38090780d0ec` — personal, `kartikhirijaganer@gmail.com` |
| Offer / spending limit | `PayAsYouGo_2014-09-01`, **spending limit Off** — no hard stop exists |
| Existing resource groups | `fraudlens-tfstate-rg`, `bigdatacloudcomputing`, `NetworkWatcherRG` |
| AKS / ACR / Container Apps / budgets | **none** |
| Terraform state blobs | `dev` (181 B, empty), `prod` (181 B, empty), `data-batch` (applied, then destroyed). **No `aks-demo` blob.** |
| OIDC app `b0dd60f1-…` | `Contributor` + `User Access Administrator`, subscription scope — sufficient |
| OIDC federated subjects | `environment:production`, `environment:Production`, `refs/heads/dev`, `refs/heads/main` |
| Providers | ContainerService ✅ ContainerRegistry ✅ Compute ✅ Consumption ✅ CostManagement ✅ `microsoft.insights` ✅ OperationalInsights ✅ · **`Microsoft.App` NotRegistered** ❌ |
| AKS versions | 1.36, **1.35 (default)**, 1.34, 1.33/1.32/1.31 LTS |
| GitHub repo | **PUBLIC** → Actions minutes unlimited and free |

**OIDC works from this branch.** All eight `deploy-aks.yml` jobs declare `environment: production`
(`.github/workflows/deploy-aks.yml:56,78,111,134,168,194,228,245`), matching the
`gh-env-production-lc` federated credential regardless of branch. No new credential needed.

### Quota — the binding constraint

| Region | Total regional vCPU | Spot vCPU | Notable families |
|---|---|---|---|
| **westus3** (AKS target) | **72** | **3** | BS 10, DASv4 10, DSv3 10, Dasv7 10, EADSv5 32, **DASv5 = 0** |
| eastus (Container Apps) | 10 | — | Serverless; no VM quota consumed |

westus3 stays the AKS target: 72 regional vCPUs vs 10 in eastus, and it is what ADR-021,
`budget.yaml`, and `aks-demo.tfvars` already specify.

---

## Defects — verified, with the exact fix

### D1 — Custom VNet on Container Apps costs ~$22/month for nothing  ← largest saving

`infra/terraform/environments/prod/main.tf:78` passes
`infrastructure_subnet_id = module.networking.apps_subnet_id` into `gateway_app`, which sets it on
`azurerm_container_app_environment` (`modules/gateway_app/main.tf:157`). A custom-network Container
Apps environment provisions a Standard Load Balancer and public IP as fixed infrastructure.

| | Standard LB *(list)* | Public IP | Monthly |
|---|---|---|---|
| Custom VNet environment | ~$18.25 | ~$3.65 | **~$21.90** |
| Platform-managed environment | — | — | **$0.00** |

The app makes only outbound calls to public Supabase, OpenRouter, and Infisical endpoints and
handles synthetic data. There is no private-connectivity requirement to justify the charge.

**Fix.** Make `infrastructure_subnet_id` nullable in `gateway_app`; pass `null` from `prod`; set
`apps_subnet_prefixes = []` in `prod.tfvars`. The `networking` module already gates the apps subnet
on `count = length(var.apps_subnet_prefixes) > 0 ? 1 : 0` (`modules/networking/main.tf:49`), so the
subnet simply is not created — only `output "apps_subnet_id"` needs to tolerate the empty case.
ADR-021's AKS root already passes `apps_subnet_prefixes = []`, so this is an established pattern.

> Verify at plan time: `terraform plan` for `prod` must show **no** `azurerm_virtual_network` and
> **no** `azurerm_subnet` for apps. Confirm on the first bill that no Load Balancer meter appears.

### D2 — `Standard_D2as_v5` quota is zero (apply-stopping)

`az vm list-usage -l westus3` → `Standard DASv5 Family vCPUs = 0/0`. The SKU shows as *available*
(`az vm list-skus` reports no restrictions) but availability is not quota. Any node pool using it
fails with `QuotaExceeded`. **A Spot quota increase does not help** — the family limit is zero, so
neither Spot nor on-demand D2as_v5 can be created at any size.

**Fix: `Standard_D2as_v4`, regular (on-demand) priority.** Selected on your criterion — cheapest
that stays reliable:

| SKU | Compute $/hr | Ephemeral OS disk? | Disk $/hr | **Effective** | Family quota | Verdict |
|---|---|---|---|---|---|---|
| **Standard_D2as_v4** | 0.0960 | **Yes** (53.6 GB cache) | 0.000 | **0.0960** | DASv4 = 10 ✅ | **Chosen** |
| Standard_D2s_v3 | 0.0960 | Yes | 0.000 | 0.0960 | DSv3 = 10 ✅ | Equivalent, Intel |
| Standard_D2as_v7 | 0.0908 | **No** (cache 0) | ~0.027 | 0.1178 | Dasv7 = 10 ✅ | 23% dearer overall |
| Standard_B2ms | 0.0832 | Yes | 0.000 | 0.0832 | BS = 10 ✅ | **Rejected** — burstable |
| Standard_D2as_v5 | 0.0860 | — | — | — | **DASv5 = 0** ❌ | **Cannot be created** |

`Standard_B2ms` saves **$0.05 across a whole session** and is rejected because B-series CPU credits
throttle under sustained load, and the session exists to produce a defensible CPU-driven HPA
measurement. System pool stays `Standard_B2s` ($0.0416/hr) — it runs only critical addons.

**Also blocks Spot regardless of SKU:** regional Spot is capped at 3 vCPU; the 2-node pool needs 4.
On-demand uses 4 of 72 regional vCPUs — no quota pressure, no mid-demo eviction, and node
scale-out becomes genuinely demonstrable. **No quota request is needed.** This changes an ADR-021
decision and requires an amendment (Phase 6).

### D3 — Container Apps crashes on boot (CORS format)

`modules/gateway_app/main.tf:214` emits `value = join(",", var.cors_allow_origins)`. The field is
`cors_allow_origins: list[str]` (`backend/src/fraudlens_backend/settings_gateway.py:24`) with no
custom decoder, so pydantic-settings v2 parses the env var as **JSON**. `[]` produces `""` → decode
error at startup; a populated list produces `https://x` → also invalid JSON.

**Fix.** `value = jsonencode(var.cors_allow_origins)`. The Kubernetes overlay already does this
correctly, so it is a consistency fix. (The Vercel proxy in D6 makes browser requests same-origin,
so CORS is not load-bearing for the UI — but the setting must still parse or the app will not boot.)

### D4 — No cap on LLM spend

`config/default.yaml` has a request rate limit (120/min) but **no LLM spend or live-run quota**
exists anywhere — verified by grep across `settings*.py`, `config/default.yaml`, `config/prod.yaml`,
and `config/llm/`. With `llm_mode: live` on a public URL, 120 requests/minute of investigations has
no spend ceiling on OpenRouter.

**Fix.** Add a daily spend ceiling to `AppSettings` (new field, `Field(..., description=...)` per
convention), default `$0.25/day` in `prod.yaml`, enforced fail-closed in the investigation path —
over budget returns the standard `{code, message, details, requestId}` envelope, never a raw error.
Maximum theoretical exposure becomes $7.50/month; normal usage is far below.

### D5 — Uncapped log ingestion

`modules/observability/main.tf:34-39` creates the workspace with `sku = "PerGB2018"` and
`retention_in_days` but **no `daily_quota_gb`**. Ingestion bills at $2.30/GB with no ceiling.

**Fix.** Add `daily_quota_gb = 0.1` (≈$6.90/month worst case, ~$1.15 expected). The workspace stops
ingesting past the cap rather than billing on.

### D6 — The frontend and backend are on different origins

`VITE_API_BASE_URL` would point the browser at the raw Container Apps FQDN, meaning a second public
hostname to share, a CORS dependency, and a URL that changes if the app is recreated.

**Fix: same-origin proxy via Vercel rewrite.** `/api/:path*` → `${AZURE_API_ORIGIN}/api/:path*`,
with production `VITE_API_BASE_URL=""`. The recruiter-facing URL stays
`https://fraud-lens-amber.vercel.app` — already live, already in `FRONTEND_URL`. The API rewrite
must precede the SPA fallback, and authenticated/streaming (SSE) requests must be forwarded
unchanged and uncached.

### D7 — Terraform will clobber the blue/green promotion

`gateway_app` has **no `lifecycle { ignore_changes }`** and hardcodes
`traffic_weight { latest_revision = true, percentage = 100 }` (`main.tf:186-197`). The deploy job
stages at 0%, gates, then promotes; the next `terraform apply` would reset traffic to latest,
bypassing the gate. The 2026-08-17 plan flagged this as **critical**; never implemented.

**Fix.** `lifecycle { ignore_changes = [template[0].container[0].image, ingress[0].traffic_weight, secret] }`

### D8 — Container Apps has no secret injection at all

`grep -rn 'DATABASE_URL|SUPABASE|OPENROUTER|INFISICAL' infra/terraform/**/*.tf` → **zero matches**.
Under `llm_mode: live`, `/readyz` requires all five probes `ok`; `database` would be `skipped`,
`infisical` and `llmProvider` `down` → 503 → smoke fails → deploy aborts.

**Fix: inject at deploy time, never through Terraform.** `deploy-backend.yml` already authenticates
to Infisical via OIDC. Add a step using `az containerapp secret set` + `--set-env-vars
KEY=secretref:name`. Values never touch tfvars or state, satisfying Golden Rule 3 and ADR-010.
Terraform owns the app, scaling, non-secret env, and secret *reference names*; the deploy helper
owns secret *values*. `ignore_changes = [secret]` stops Terraform reverting them.

### D9 — `/readyz` makes outbound calls on every platform probe

`/readyz` performs a Supabase JWKS fetch and an OpenRouter `/models` call per invocation
(`backend/src/fraudlens_backend/api/ops.py`). At a 10-second probe period that is ~8,640 outbound
OpenRouter calls per day purely for health checking — enough to hit provider rate limits and add
latency to every probe.

**Fix.** Cache the two remote probe results for 5 minutes; keep database and local Chroma checks
per-request (they are cheap and are the ones that actually fail). Set the Container Apps readiness
probe to 30 s with 3 consecutive failures. Response envelope and fail-closed semantics unchanged —
the `infisical` probe still reports a **count**, never key names.

### D10 — Seven undefined repo variables

`deploy-aks.yml` references, and `gh variable list` does not contain: `AKS_DEPLOY_ENABLED`,
`AKS_ADMIN_OBJECT_ID`, `AKS_OPERATOR_CIDR`, `AZURE_BUDGET_CONTACT_EMAIL`,
`AZURE_BUDGET_START_DATE`, `INFISICAL_AKS_IDENTITY_ID`. `deploy-frontend.yml` needs
`VERCEL_DEPLOY_ENABLED`. This plan adds `KEEP_WARM_ENABLED`, `BACKEND_URL`, and `AZURE_API_ORIGIN`.

### D11 — AKS API allowlist versus multi-runner workflow

The `aks` module has a precondition requiring non-empty `authorized_ip_ranges`, and `deploy-aks.yml`
splits work across **eight jobs on eight different runners**, each with a different public IP. Only
one can be allowlisted, so the later jobs cannot reach the API server.

**Fix.** Consolidate apply → credentials → operator → deploy → smoke → HPA → evidence → teardown
into **one job on one runner**, which discovers its own `/32` and allowlists exactly that. Add a
4-hour wall-clock deadline and an `if: always()` teardown step so the cluster is destroyed even
when a middle step fails.

### D12 — Miscellaneous

| # | Issue | Fix |
|---|---|---|
| a | `deploy/k8s/overlays/aks-demo/aks-demo.env` sets `FRAUDLENS_QUEUE_BACKEND=container_apps_jobs`, but the AKS root creates no Container Apps | Set `local`; the in-cluster worker claims from Postgres per ADR-027 |
| b | No Ingress or LoadBalancer anywhere — Service is `ClusterIP`, reachable only by port-forward | `LoadBalancer` Service patch in the aks-demo overlay only (+$0.005/hr) |
| c | `.checkov.yaml` scans only `data-batch` and `aks-demo`; `dev`/`prod` unscanned | Add both |
| d | `max_replicas = 5` on a $25 budget | `max_replicas = 1` — a hard ceiling that cannot be exceeded |
| e | AKS overlay carries `replace-*` placeholders and a hardcoded registry path | Fail rendering if any `replace-*` remains after substitution |
| f | Scheduled retrain Container Apps Job creates recurring compute | Manual-only |
| g | Branch was `releas/v0.4.0` (typo); workflows trigger on `release/*` | **DONE 2026-09-15** — renamed to `release/0.4.0`, matching the `release/0.3.0` convention (branches carry no `v`; tags do) |
| h | Backend image never pushed; local host is arm64 (M4 Pro), AKS nodes are x86-64 | Always build in Actions (`ubuntu-latest`); verify the GHCR package is public before apply |

---

## Cost projection

Rates pulled live from the Azure Retail Prices API on 2026-09-15 for the actual target regions.
Marked *(list)* where the retail API did not return the meter.

### Container Apps — the permanent URL (eastus, Consumption plan)

| Meter | Rate |
|---|---|
| Standard vCPU **Active** | $0.000024 / second |
| Standard vCPU **Idle** | $0.000003 / second |
| Standard Memory Active / Idle | $0.000003 / GiB-second |
| Standard Requests | $0.40 / 1M |

**Free monthly grant: 180,000 vCPU-seconds + 360,000 GiB-seconds + 2M requests.** At 0.5 vCPU /
1 GiB that is exactly **100 warm replica-hours per month at zero cost** (memory binds first). A
replica that exists but is not serving bills at the **idle** rate — 8× cheaper than active. That is
what makes keeping one warm cheap.

| Warm window | Replica-hours/mo | Beyond grant | **Compute/mo** | Cold start |
|---|---|---|---|---|
| Pure scale-to-zero | ~4 | 0 | $0.00 | 20–75 s per visit |
| Weekdays ~4.5 h/day | 99 | 0 | $0.00 | none in-window |
| **Weekdays 8 h/day (chosen)** | **176** | **76 h** | **$1.23** | **none in-window** |
| Weekdays 10 h/day | 220 | 120 h | $1.95 | none in-window |
| 24/7 (`min_replicas = 1`) | 730 | 630 h | $10.20 | never |

**Chosen: `min_replicas = 0`, `max_replicas = 1`, plus a keep-warm ping weekdays 8 h/day.** The
cheapest configuration that meets your requirement — instant response during the hours a recruiter
would click, nothing overnight or at weekends. `max_replicas = 1` is a hard cap: even under a
traffic spike the app cannot scale into a large bill. The ping is a GitHub Actions cron; the repo is
public, so Actions minutes are free.

**Full monthly cost, with the VNet removed:**

| Item | Cost/mo |
|---|---|
| Compute — 176 warm hours, idle rate, beyond grant | $1.23 |
| Log Analytics — capped at 0.1 GB/day; expected ~0.5 GB/mo @ $2.30 | ~$1.15 (max $6.90) |
| Blob storage — artifacts / SAR PDFs, a few GB hot LRS | ~$0.10 |
| Custom VNet Load Balancer + public IP | **$0.00** — removed (D1) |
| ACR — `acr_enabled = false`, images on public GHCR | $0.00 |
| Keep-warm cron — public repo | $0.00 |
| **Total** | **≈ $2.48/mo** |

| Configuration | Monthly |
|---|---|
| **As planned** | **~$2.50** |
| As currently coded (custom VNet, no caps) | ~$23.25 |
| Worst case if a crawler drives active-rate traffic all month at 1 replica | ~$28 + LLM |
| With `min_replicas = 1` instead of keep-warm | ~$11.45 |

### AKS — per ephemeral session (westus3)

| Item | Rate | Hours | Cost |
|---|---|---|---|
| AKS control plane — Free tier | $0.0000/hr | 4.0 | $0.000 |
| 1× `Standard_B2s` system node | $0.0416/hr | 4.0 | $0.166 |
| 1× `Standard_D2as_v4` user node (baseline) | $0.0960/hr | 4.0 | $0.384 |
| 2nd `Standard_D2as_v4` during scale-out | $0.0960/hr | 1.0 | $0.096 |
| Ephemeral OS disks (both pools) | $0.0000 | — | $0.000 |
| Standard Load Balancer, first 5 rules *(list)* | $0.0250/hr | 4.0 | $0.100 |
| Public IP × 2 (egress + Service) | $0.0050/hr ea | 4.0 | $0.040 |
| Log Analytics — `monitoring_enabled = false` | $0.0000 | — | $0.000 |
| **Total per 4-hour session** | | | **≈ $0.79** |

With the ADR-028 30% margin: **$1.03 projected**. Hard admission ceiling: **$5.00 per session**.

| AKS mode | Monthly | Note |
|---|---|---|
| **Destroyed between sessions (chosen)** | **$0** | — |
| `az aks stop` between sessions | ~$25 | Nodes deallocate; LB + IPs keep billing. **Stop is not teardown** (ADR-028) |
| Running 24/7, 1 user node | ~$126 | Exhausts the remaining $65.84 in ~15 days |

### Combined and budget fit

| Line | Amount |
|---|---|
| Container Apps permanent URL | ~$2.48/month **recurring** |
| Terraform state storage (exists) | ~$0.02/month |
| **Fixed monthly total** | **≈ $2.50/month** |
| AKS, per session (variable — $0 in a month with none) | ~$0.79 |
| Example month with 4 AKS sessions | ≈ $5.66 |

| Budget position | Amount |
|---|---|
| One-time experiment ceiling (`config/experiments/budget.yaml`) | $75.00 |
| Spent and settled (data-batch $3.24 + GPU benchmark $5.92) | $9.16 |
| **Remaining** | **$65.84** |
| AKS sessions this buys at $1.03 projected each | **~63** |

**The recurring cost needs its own instrument.** ADR-028's "reconsider when" clause names this:
*"the project gains recurring production workloads (which need operational budgets/SLOs, not this
protocol)."* The $75 ceiling is defined as **one-time**, and the ledger validator treats every row
as drawing against it — a monthly charge has no correct home there. Phase 6 adds ADR-029
establishing a separate recurring operational budget. At $2.50/month the amount is small; the
bookkeeping distinction is what keeps the experiment ceiling meaningful.

---

## Phase 0 — Pre-flight: unblock the gates, lock identity, admit the budget

**What.** Repair the broken documentation gate that blocks every other phase, lock deployment
authority to your personal identity, and open the governance paperwork before anything billable
exists.

**Why.** ADR-028: *"Before creation it must have a current-plan ledger row, an allocation and
watchdog in `config/experiments/budget.yaml`, a current provider quote, and an explicit human
approval."* And the identity guard mechanically prevents deploying under the work GitHub account —
`gh auth status` shows both accounts logged in, so this is a live hazard, not a theoretical one.

**How.**

1. **Unblock the gates: repair the 13 dead plan links.** `make docs-links-check` — and therefore
   `make ci` and `make pre-pr`, which every phase below ends with — **fails today**. Three plan
   files are staged for deletion while **10 ADRs and 3 rows of `plans/README.md` still link to
   them**. Nothing else in this plan can be verified until this is fixed, so it goes first.

   The dead references, all pointing at files staged for deletion:

   | Where | Count | Current form |
   |---|---|---|
   | `docs/architecture/adr/ADR-019` … `ADR-028` (the `- **Related:** implementation plan` line) | 10 | Markdown link `[\`plans/<file>.md\`](../../../plans/<file>.md)` |
   | `plans/README.md` "Active plans" table | 3 | Markdown link in the plan column |

   **Follow the repo's own retirement convention**, stated at `plans/README.md:47-49`: *"Retired
   plan files are intentionally absent from the working tree… commit hashes identify the history
   transition without creating dead Markdown links."* The existing Retired rows show the exact
   shape — filename in **backticks as plain text, never a link**, plus the removing commit and a
   live link to the maintained replacement. So:
   - **ADRs (10):** convert the `Related:` plan reference from a Markdown link to backticked plain
     text — `` `plans/<file>.md` (retired; see plans/README.md) ``. The filename stays visible and
     greppable; the dead link disappears. Do **not** repoint them at this plan — these ADRs record
     decisions from release 0.3, not 0.4.
   - **`plans/README.md`:** move the three rows out of "Active plans" into the "Retired plans"
     table, each with its removing commit and a maintained replacement (ADR-019 for the 2026-08-17
     plan; ADR-020 through ADR-028 plus the published benchmarks for the 2026-09-13 AKS/vLLM plan;
     `make pr-check` and `scripts/check_pr_title.sh` for the local-PR-check plan). Then add **this**
     plan as the sole Active row.

   **Sequencing constraint — this needs two commits.** All three files are still present at `HEAD`
   (`fe2e563`); the deletions are staged only, so the removing commit **does not exist yet**, and a
   commit cannot contain its own hash. The existing Retired rows cite the removing commit (verified:
   the files are absent at `4cd0e63` and `ef51c0c`), so match that:
   - **Commit A** — delete the three plan files, convert the 10 ADR links to plain text, and move
     the three README rows into the Retired table with the *Removing commit* cell left as `TBD`.
     `docs-links-check` passes here, because it validates Markdown links only and a backticked hash
     is not one.
   - **Commit B** — replace the three `TBD` cells with Commit A's short hash.

   If you would rather keep the plan files instead, `git restore --staged --worktree plans/<file>.md`
   on all three also makes the gate pass — but it contradicts the convention your own README states,
   so the retirement path above is the one this plan takes.

   **Verify:** `make docs-links-check` reports 0 invalid links, and `grep -rn 'plans/2026-08-17\|plans/2026-09-13' docs/architecture/adr/`
   returns no Markdown links.

2. **Add `scripts/check_deploy_identity.sh`**, invoked first by every deploy workflow and by
   `make pre-pr`. Fails on any of:
   - repository ≠ `Kartik-Hirijaganer/FraudLens`
   - `git config user.email` ≠ `65550498+Kartik-Hirijaganer@users.noreply.github.com`
   - `origin` ≠ `git@github-personal:Kartik-Hirijaganer/FraudLens.git`
   - Azure subscription ≠ `01417138-33d6-4b26-a0e2-38090780d0ec` or tenant ≠ `057e49df-…`
   - any unresolved `replace-*` placeholder in rendered Terraform or Kubernetes output

3. **Rename the branch** — **DONE 2026-09-15.** `releas/v0.4.0` → `release/0.4.0`.
   `deploy-backend.yml` triggers on `branches: [dev, "release/*"]`, so the misspelling would have
   silently never fired it. The branch had no upstream, did not exist on `origin`, and carried no
   commits ahead of `main`, so the rename was purely local — no history rewrite, no remote cleanup,
   and the three staged plan deletions were preserved untouched. Naming follows the repo's own
   convention from `release/0.3.0`: **branches omit the `v`, tags carry it** (`v0.3.0`).

4. **Configure the GitHub `production` environment** with yourself as required reviewer. This is
   the Golden Rule 7 human gate for every Azure job. Verify it actually blocks by dispatching a
   no-op run.

5. **Rebalance `config/experiments/budget.yaml`.** The validator
   (`scripts/lib/experiments/budget.py`) enforces that allocation **names** are exactly the five in
   `REQUIRED_ALLOCATIONS` and values sum **exactly** to `ceiling_usd`. A new `aks` key fails.
   Rebalance within existing names — the GPU benchmark is complete and used $5.92 of $25:

   | Allocation | Before | After |
   |---|---|---|
   | `azure_cpu_batch` | 15.00 | 15.00 |
   | `gpu_benchmark` | 25.00 | **10.00** |
   | `e2e_application_pass` | 5.00 | 5.00 |
   | `supporting_resources` | 5.00 | **20.00** |
   | `reserve` | 25.00 | 25.00 |
   | **Sum** | 75.00 | **75.00** ✅ |

6. **Add dated rate quotes** to the `rates:` map (free-form; adding keys is safe):
   `azure_d2as_v4_payg = 0.096000`, region westus3, `purchase_option: payg`, with
   `price_source_url` and `price_verified_at: 2026-09-15`. Confirm `azure_b2s_payg = 0.041600`
   still matches live pricing (it does).

7. **Update the `aks-plan` estimate** at `Makefile:809-812` — currently
   `azure_b2s_payg + 2 × azure_d2as_v5_spot`. Change to `azure_b2s_payg + 2 × azure_d2as_v4_payg`
   and extend the caveat to name the LB and public IPs it excludes.

8. **Record the quota reality** in the "Day-1 owner actions" table of
   `docs/reference/experiments/ledger.md`: `Standard DASv5 Family = 0` (blocks D2as_v5),
   `Total Regional Low-priority = 3` (blocks the Spot pool), `Standard DASv4 Family = 10`
   (supports the chosen SKU), all verified 2026-09-15. Note explicitly that **no quota request is
   required**, since a Spot increase would not lift the zero family limit.

9. **Open a ledger row:** run id `aks-demo-20260915-01`, provider Azure, SKU
   `Standard_B2s + 2×Standard_D2as_v4`, PAYG, allocation `supporting_resources`, ceiling $5.00,
   teardown verified **no**.

**Files.** `docs/architecture/adr/ADR-019` … `ADR-028` (the `Related:` line only), `plans/README.md`,
the three staged plan deletions, `scripts/check_deploy_identity.sh` (new),
`config/experiments/budget.yaml`, `Makefile`, `docs/reference/experiments/ledger.md`.

**Tests.** `make docs-links-check` · `make experiment-budget-check` ·
`uv run python scripts/experiment_budget.py ledger-check` · identity-guard unit tests covering each
refusal path. Then `make pre-pr` end-to-end — it must pass before Phase 1 starts, since every
later phase ends with it.

**Acceptance.** `make docs-links-check` reports **0 invalid links** (down from 13) and `make pre-pr`
passes clean. Budget check passes with allocations summing to exactly 75.00. Ledger has an open
AKS row. The identity guard refuses a simulated work-account remote. The production environment
demonstrably blocks a dispatched run pending your approval. **Zero Azure resources created.**

---

## Phase 1 — Fix the defects (code only, still $0)

**What.** Correct all twelve defects without applying anything.

**Why.** Every one of them fails, or overcharges, *after* you have started paying. Fixing them at
zero spend is free.

**How.**

| # | File | Change |
|---|---|---|
| D1 | `modules/gateway_app/main.tf`, `environments/prod/{main.tf,prod.tfvars}` | Make `infrastructure_subnet_id` nullable; pass `null`; `apps_subnet_prefixes = []`. Guard `output "apps_subnet_id"` for the empty case. **~$22/mo saving** |
| D1 | `modules/gateway_app/main.tf` | Output the **stable** app ingress FQDN, not `latest_revision_fqdn` — the URL must survive revision promotion |
| D2 | `environments/aks-demo/aks-demo.tfvars` | `user_vm_size = "Standard_D2as_v4"` |
| D2 | `modules/aks/main.tf` | Add `user_pool_spot_enabled` (default `false`); make `priority`, `eviction_policy`, `spot_max_price`, and the Spot taint conditional. Keep the `user_max_count <= 2` release cap |
| D2 | `deploy/k8s/overlays/aks-demo/kustomization.yaml` | Drop the Spot toleration and node affinity — on an on-demand pool the taint is absent and the affinity would still steer scheduling |
| D3 | `modules/gateway_app/main.tf:214` | `value = jsonencode(var.cors_allow_origins)` |
| D4 | `backend/src/fraudlens_backend/settings*.py`, `config/prod.yaml` | New `llm_daily_budget_usd` field (Pydantic, `Field(..., description=...)`), default `0.25` in prod; fail-closed enforcement in the investigation path returning the standard error envelope |
| D5 | `modules/observability/main.tf` | `daily_quota_gb = 0.1` |
| D6 | `frontend/vercel.json`, `frontend/.env.production` | `/api/:path*` → `${AZURE_API_ORIGIN}/api/:path*`, ordered before the SPA fallback; `VITE_API_BASE_URL=""`; no caching on authenticated responses |
| D7 | `modules/gateway_app/main.tf` | `lifecycle { ignore_changes = [template[0].container[0].image, ingress[0].traffic_weight, secret] }` |
| D9 | `backend/src/fraudlens_backend/api/ops.py` | 5-minute cache on the Supabase JWKS and LLM-provider probes; DB and Chroma stay per-request. Envelope and fail-closed semantics unchanged |
| D12a | `deploy/k8s/overlays/aks-demo/aks-demo.env` | `FRAUDLENS_QUEUE_BACKEND=local` |
| D12b | `deploy/k8s/overlays/aks-demo/service-lb.yaml` (new) | `type: LoadBalancer`, `externalTrafficPolicy: Local`, health-probe path annotation `/healthz`. Base and kind overlay unchanged |
| D12c | `.checkov.yaml` | Add `environments/dev` and `environments/prod` |
| D12d | `environments/prod/prod.tfvars` | `max_replicas = 1`; retrain job manual-only |
| D12e | `scripts/lib/k8s_demo/render.py` | Fail rendering if any `replace-*` placeholder survives substitution; take the Infisical project slug as a non-secret input |
| — | `config/k8s-demo.yaml` + `scripts/lib/k8s_demo/config.py` | Add an `aks:` section for LoadBalancer smoke-URL discovery so `make aks-smoke` targets the external IP. `K8sDemoConfig` is frozen with `extra="forbid"` — the field must be declared |

**Reuse, do not rebuild.** `scripts/lib/k8s_demo/render.py:110-113` already substitutes the image
and both Infisical placeholders at render time. `make aks-verify-clean` already asserts both
resource groups, tagged resources, and the budget are gone.

**Tests.**
```bash
make tf-validate && make iac-scan
make k8s-validate && make k8s-demo-test
make aks-plan          # read-only; -backend=false, never applies
make ci
```
Add behavioral coverage for: absent VNet resources in the prod plan; stable FQDN output; warm
scaling bounds and manual jobs; `jsonencode` CORS round-trip; LLM budget fail-closed at and over
the ceiling; readiness cache hit/expiry/provider-outage/recovery; placeholder-detection refusal;
Vercel rewrite precedence and SSE forwarding.

**Acceptance.** `terraform plan` for `prod` shows **no** VNet or apps subnet and `max_replicas = 1`.
`make aks-plan` renders `Standard_D2as_v4`, regular priority, no Spot block, with an estimate
derived from `azure_d2as_v4_payg`. `make k8s-validate` passes with `LoadBalancer` in the aks-demo
render and `ClusterIP` still in kind. **Still zero Azure resources.**

---

## Phase 2 — Cost projection, alerts, and hard caps (before any spend)

**What.** A reproducible cost calculator, two budget scopes, limits that cannot be exceeded, and a
daily watchdog.

**Why.** Your spending limit is **off** and Azure budget data lags by hours, so alerts alone cannot
protect you. The hard caps are what actually bound the bill; the alerts tell you when something
unexpected is happening.

**How.**

1. **`make azure-cost-plan`** — a generated cost model, not a hand-maintained table.
   - Reads the committed ACA and AKS shapes (replicas, vCPU/memory, node SKUs and counts).
   - Queries the Azure Retail Prices API read-only for the target regions and SKUs.
   - Computes ACA idle/active monthly cost, free-grant offset, log and storage allowance, and the
     AKS per-session estimate.
   - Lists included and **excluded** services separately, so the gaps are visible.
   - **Fails** if the AKS session projection exceeds $5.00 or the ACA config exceeds one replica.
   - Writes `docs/reference/cost-model.md` with a generation date and per-rate source URLs,
     matching the provenance convention `budget.yaml` already uses.
   - Unit-tested against frozen price fixtures so the calculator is deterministic in CI.

2. **Keep the RG-scoped AKS budget.** `modules/budget/` already emits an
   `azurerm_consumption_budget_subscription` filtered to `ResourceGroupName In [...]` with four
   notifications. `aks-demo` wires it at $15 across both AKS groups. No change.

3. **Add a subscription-wide safety net.** New root `environments/cost-guardrails/` using the same
   module with **no dimension filter**, `amount_usd = 25`, monthly — the only thing that catches a
   resource created in an unplanned group. Budgets are free.
   - New backend key `cost-guardrails.terraform.tfstate` in the existing container.
   - The module currently *requires* non-empty `resource_group_names`; make the `filter` block
     `dynamic` so one module serves both scopes.
   - Notifications: 50% actual, 80% actual, 100% actual, 100% forecast → your email.

4. **Wire a budget into `prod`** at $25. Neither `dev` nor `prod` has one today, and `prod` becomes
   the always-on cost surface.

5. **Hard limits — the controls that actually bind.** These are ceilings, not alerts:

   | Control | Value | Where |
   |---|---|---|
   | ACA maximum replicas | **1** | `prod.tfvars` (D12d) |
   | Log ingestion | **0.1 GB/day** | `observability` (D5) |
   | LLM spend | **$0.25/day** | `prod.yaml` (D4) |
   | Scheduled jobs | **none** — manual only | `prod.tfvars` |
   | AKS lifetime | **4-hour deadline + always-teardown** | workflow (Phase 5) |
   | AKS session cost | **$5.00 admission ceiling** | `azure-cost-plan`, ledger |

6. **Daily leftover-resource watchdog** — `.github/workflows/cost-watchdog.yml`,
   `schedule: cron "0 13 * * *"` + dispatch, `environment: production`, **read-only `az` only**:
   `az group exists` for both AKS groups · `az aks list` · `az resource list --tag project=fraudlens`
   · `az consumption budget list` · month-to-date Cost Management query. Fails the run — emailing
   you through normal Actions notifications — if an AKS group survives or MTD cost exceeds a
   threshold. Read-only, so Golden Rule 7 does not gate it. Catches a forgotten teardown within
   24 hours rather than at a budget threshold, which on a $15 budget could be a week of burn.

7. **Keep-warm workflow** — `.github/workflows/keep-warm.yml`,
   `schedule: cron "*/4 13-21 * * 1-5"` (every 4 min, weekdays 13:00–21:00 UTC = 09:00–17:00 ET)
   plus dispatch. Body is one `curl -fsS "${{ vars.BACKEND_URL }}/healthz"` — no Azure login, no
   OIDC, no secrets. Gated `if: ${{ vars.KEEP_WARM_ENABLED == 'true' }}` so it stays inert until
   Phase 3 yields the FQDN and can be switched off in one variable. Concurrency group so slow runs
   cannot stack. Free — public repo.

   > GitHub scheduled triggers are best-effort and can be delayed under load; treat window edges as
   > soft. A missed ping costs one cold start, not money.

8. **Register the Container Apps provider** — `az provider register -n Microsoft.App --wait`. Free,
   idempotent, must precede Phase 3.

**Optional — automated shutdown at 80% budget.** The only true automated stop, since budgets do not
halt spend. An Azure Automation account (free tier covers 500 job-minutes/month) with a
system-assigned identity and a **custom role limited to reading/deactivating Container Apps
revisions and reading/stopping AKS clusters** — no create, delete, role-assignment, storage-data, or
secret permissions. Triggered by the 80% budget action group; validates the common alert schema and
exact budget name; acts only on resources tagged `project=fraudlens`; idempotent under repeated
notifications; logs resource names, actions, and outcomes only.

I am **not** making this core scope. With `max_replicas = 1`, a 0.1 GB/day log cap, a $0.25/day LLM
ceiling, and destroy-after-use AKS, the maximum plausible monthly bill is bounded around $28 — the
hard caps already do the work, and this adds a custom RBAC role and a runbook to maintain. Take it
if you want belt-and-braces; it is fully specified here either way.

**Files.** `scripts/azure_cost_plan.py` + `make azure-cost-plan` (new),
`modules/budget/main.tf` (dynamic filter), `environments/cost-guardrails/*` (new),
`environments/{dev,prod}/main.tf` + `.tfvars`, `docs/reference/cost-model.md` (generated),
`docs/README.md`, `.github/workflows/{cost-watchdog,keep-warm}.yml` (new).

**Tests.** `make azure-cost-plan` against frozen fixtures · `make tf-validate` · `make iac-scan` ·
`make docs-check` · `make docs-links-check` · budget notification and action-group wiring tests.

**Acceptance.** `terraform plan` for `cost-guardrails` shows exactly one
`azurerm_consumption_budget_subscription` and nothing else. `make azure-cost-plan` passes its $5
AKS ceiling and one-replica assertion, and regenerates `docs/reference/cost-model.md` with dated,
sourced rates. `az provider show -n Microsoft.App` → `Registered`.

> **Gate — human approval required (Golden Rule 7).** Applying `cost-guardrails` is the first
> `terraform apply`. It creates only a budget (non-billable), but it is a mutating cloud action.
> Everything before this point is $0 and read-only.

---

## Phase 3 — Validate everything locally before touching Azure

**What.** Run the full repository, deployment, security, and local Kubernetes gates.

**Why.** Azure must not be the debugging environment. `make kind-demo` exercises the identical
Kustomize base at $0 and will surface manifest errors before you are paying for a cluster.

**How.** In order:
```bash
make docs            # regenerate, then confirm clean
make k8s-tools-check
make k8s-validate
make k8s-demo-test
make quality-gates
make hpa-evidence-validate
make kind-demo       # image → up → load → deploy → smoke → hpa-demo, trap teardown
make azure-cost-plan
make docs-check
make pre-pr
drift-check plans/2026-09-15-azure-deployment-cost-projection-and-budget-alerts.md phase=1
drift-check ... phase=2
drift-check ... phase=3
```

**Acceptance.** `make pre-pr` passes with ≥90% coverage on both stacks. Citation precision/recall,
unsupported-claim, clean-draft false-positive, and model-egress gates pass. The kind proof shows
HPA scale-up/down and worker recovery. Terraform plans contain only reviewed resources and no
secret values. **You review the plans and the projected cost before any apply.**

---

## Phase 4 — Container Apps: the permanent live URL

**What.** First real apply. Stand up `fraudlens-prod` on a platform-managed network, wire secret
injection, deploy, promote, and route Vercel at it.

**Why.** This is what a recruiter clicks. It is also the cheaper and lower-risk apply, so it goes
first — if OIDC, Infisical, or the image is wrong, you find out at ~$0.08/day rather than
mid-AKS-session.

**How.**

1. **Verify the image is pullable.** `deploy-backend.yml` builds on `ubuntu-latest` (x86-64) and
   pushes `ghcr.io/kartik-hirijaganer/fraudlens-backend:<sha>`. Confirm the GHCR package is
   **public** — with `acr_enabled = false` the Container App pulls anonymously and a private
   package fails the pull with a confusing error. Fail rather than silently switch registries.

2. **Set repo variables:** `AZURE_BUDGET_CONTACT_EMAIL`, `AZURE_BUDGET_START_DATE`
   (`^\d{4}-\d{2}-01T00:00:00Z$`). `AZURE_API_ORIGIN` and `BACKEND_URL` come after the apply.

3. **Add secret injection** to `deploy-backend.yml`, after the existing Infisical OIDC fetch and
   **before** smoke. Allowlist exactly: `DATABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
   `FRAUDLENS_DEMO_AUTH_PASSWORD`, `OPENROUTER_API_KEY`; derive JWKS URL and issuer from
   `SUPABASE_URL`.
   ```
   az containerapp secret set -n $APP_NAME -g $RESOURCE_GROUP --secrets <name>=<value> …
   az containerapp update     -n $APP_NAME -g $RESOURCE_GROUP \
     --set-env-vars DATABASE_URL=secretref:database-url …
   ```
   Values come from the Infisical step's masked outputs; they never enter Terraform state, tfvars,
   shell tracing, logs, or artifacts. `ignore_changes = [secret]` (D7) stops Terraform reverting
   them; add a drift test asserting a later apply does not remove them.

4. **Keep `min_replicas = 0`** in `prod.tfvars` — already the committed value. Warmth comes from the
   Phase 2 cron, not a paid floor. This is what makes the URL ~$1.23/month of compute instead of
   ~$10.20. `max_replicas = 1` from Phase 1 caps the ceiling.

5. **Set `gateway_cors_origins = ["https://fraud-lens-amber.vercel.app"]`** — safe now because of
   the `jsonencode` fix, and a defence-in-depth backstop behind the same-origin proxy.

6. **Approve and flip** `AZURE_DEPLOY_ENABLED=true` (`gh variable set` is itself an `ask`
   permission), then let `deploy-backend.yml` run its existing
   `infra → build-push → stage → migrate → smoke → promote` sequence. The abort path already
   deactivates the staged revision and leaves the previous one at 100% if migrate or smoke fails.

7. **Authenticated production smoke**, not just ops probes:
   - provision the existing synthetic demo personas and bootstrap the deterministic portfolio story
   - obtain a short-lived Supabase JWT for the analyst persona using the public synthetic password;
     **mask it immediately**; expose it only to the smoke process as `SMOKE_AUTH_TOKEN`
   - exercise `/api/v1/me` role and tenant, dashboard, transaction lookup, alert view, one
     permitted investigation, and citation links resolving to the expected regulatory evidence
   - assert auditor mutations return 403 and a mismatched `agency_id` returns the safe 404 envelope
   - assert SSE streaming survives the proxy
   - inspect logs for secret, PHI, JWT, and stack-trace leakage

8. **Wire the Vercel proxy.** Set `AZURE_API_ORIGIN` to the **stable** ingress FQDN (not a revision
   FQDN — see D1). Deploy with a pinned CLI: `vercel pull --environment=production` →
   `vercel build --prod` → `vercel deploy --prebuilt --prod`. Verify the resulting URL equals
   `FRONTEND_URL`. Then set `VERCEL_DEPLOY_ENABLED=true`.

9. **Measure the real cold start**, then enable keep-warm:
   ```bash
   sleep 400   # force scale-to-zero
   curl -o /dev/null -s -w 'cold: %{time_total}s\n' https://<app_fqdn>/healthz
   curl -o /dev/null -s -w 'warm: %{time_total}s\n' https://<app_fqdn>/healthz
   ```
   Record both in `docs/reference/cost-model.md`. My 20–75 s figure is derived from the module's
   `cold_start_budget_seconds`, **not a measurement** — the real number depends mostly on image pull
   size. Then set `BACKEND_URL` and `KEEP_WARM_ENABLED=true`. If cold start proves to be under ~5 s,
   leave keep-warm disabled and the URL costs ~$1.25/month instead of ~$2.50.

**Files.** `.github/workflows/deploy-backend.yml`, `environments/prod/prod.tfvars`,
`frontend/vercel.json`, `docs/reference/cost-model.md`, repo variables.

**Acceptance.** `https://fraud-lens-amber.vercel.app` serves the frontend and proxies authenticated
APIs to a warm backend. `/readyz` returns 200 with **all five probes `ok`** (under `llm_mode: live`
a `skipped` probe fails the aggregate). The full authenticated smoke passes. A forced bad revision
demonstrates rollback with no visible downtime. The stable FQDN survives promotion. No secret value
in any log, state file, or `/readyz` body — the `infisical` probe reports a **count**, never names.

> **Cost verification gate — 48 hours after apply.** Confirm the run-rate matches ~$2.50/month:
> ```bash
> az rest --method POST \
>   --url "https://management.azure.com/subscriptions/01417138-33d6-4b26-a0e2-38090780d0ec/providers/Microsoft.CostManagement/query?api-version=2023-11-01" \
>   --body '{"type":"ActualCost","timeframe":"MonthToDate","dataset":{"granularity":"Daily","aggregation":{"totalCost":{"name":"Cost","function":"Sum"}},"grouping":[{"type":"Dimension","name":"MeterCategory"}]}}'
> ```
> **Any Load Balancer meter means the VNet removal did not take effect** (+$22/month) — stop and
> fix. An *"Environment Management"* meter would mean a workload-profile environment was created
> instead of Consumption (+$73/month) — stop and fix. Do not leave the environment standing until
> this passes.

---

## Phase 5 — AKS: one governed session, evidence, verified teardown

**What.** The apply release 0.3 deferred. Create, deploy, prove, capture, destroy — in one bounded
run.

**Why.** ADR-021: *"Never cite the kind evidence as an observed AKS deployment."* The artifact this
phase produces is the only thing that upgrades the claim.

**How.**

1. **Prerequisites.**
   - Create the Infisical Azure machine identity scoped to the actual `prod` paths `/` and `/llm`,
     authorized for the cluster's `kubelet_identity_object_id`. Record as
     `INFISICAL_AKS_IDENTITY_ID`. **This is a human action in the Infisical console — no agent may
     create it or invent its id** (ADR-028).
   - Set `AKS_ADMIN_OBJECT_ID` (`az ad signed-in-user show --query id -o tsv`).
   - `AKS_OPERATOR_CIDR` is **discovered by the runner at execution time**, not preset — see step 2.
   - **No quota request is needed** (D2).

2. **Consolidate `deploy-aks.yml` into one job** (D11). Eight jobs on eight runners cannot share one
   API-server allowlist. The single job:
   - discovers and validates its own public `/32`
   - passes it as `authorized_ip_ranges` to Terraform
   - performs apply → credentials → operator install → deploy → smoke → HPA → evidence → teardown
   - requires typed confirmation `deploy-fraudlens-aks-demo-and-destroy`
   - enforces a **4-hour wall-clock deadline**
   - runs teardown under `if: always()` so the cluster dies even when a middle step fails
   - uploads evidence **even on failure**

3. **Or run locally, gated step by step** — each line a separate approval:
   ```
   make aks-plan                                   # free, read-only, run first
   CONFIRM=yes make aks-up                         # ← first billable action
   CONFIRM=yes make aks-credentials
   CONFIRM=yes make aks-operator-install
   CONFIRM=yes make aks-secrets-operator
   CONFIRM=yes make aks-deploy IMAGE_TAG=<commit-sha>
   CONFIRM=yes make aks-smoke
   ```
   `aks-operator-install` compares `kubectl config current-context` against
   `terraform output -raw cluster_name` and refuses on mismatch — that is the tenant-safety check.
   Do not bypass it. `aks-up` starts the ledger clock.

4. **Authenticated load, not just `/healthz`.** The load Job currently runs `LOAD_MODE=healthz`,
   which proves autoscaling mechanics but not that the *application* scales. Acquire a short-lived
   Supabase token at runtime, create a namespaced Secret from stdin, reference it from the Job, and
   attach `Authorization: Bearer …` to protected transaction and investigation calls. Never publish
   the token; delete the Secret and Job immediately after evidence capture.

5. **Capture evidence** — `CONFIRM=yes make aks-hpa-demo`, then `make hpa-evidence-validate`. The
   artifact must match the shape of `docs/reference/benchmarks/k8s-hpa-scaling.md`: evidence
   SHA-256, measured commit, platform and Kubernetes version, node-pool shapes, rendered manifest
   hash and image digest, replica range, first scale-up seconds, scale-back seconds, request
   totals, durable runs completed, max worker attempt, samples table, elapsed cluster lifetime,
   cost projection, and a **Disclosures** section naming the paid session, the run id, and the SKU
   substitution from ADR-021.

   **Redact:** no kubeconfig, tokens, secret values, subscription ids, email addresses, or runner
   IPs in any published artifact.

6. **Expect different numbers from kind, and say so.** kind measured 1→5→1, first scale-up 46 s,
   scale-back 91 s, CPU peak 998%, on a laptop with `requests.cpu: 100m`. On AKS the base manifests
   apply (`requests.cpu: 500m`) on 2-vCPU nodes, so pod scale-out binds on node capacity and
   triggers the cluster autoscaler — node provisioning adds ~2–4 minutes to the tail. That is a
   **better** demonstration (pod HPA *and* node autoscaling). Do not reconcile against kind's
   figures. Report what AKS measured.

7. **Teardown — all four steps, in order:**
   ```
   CONFIRM=yes make aks-down
   make aks-verify-clean
   uv run python scripts/experiment_budget.py ledger-check
   ```
   `aks-verify-clean` fails if either resource group, any tagged/prefixed resource, or the budget
   remains. Also assert the AKS Terraform state contains no managed resources. If automated destroy
   fails, mark the workflow failed and execute the separately approved rescue destroy immediately.

8. **Complete the ledger row:** actual hours, rate, projection, evidence run id, **teardown verified
   = yes**. `check_ledger` fails publication if a report's `runId` is absent from the ledger. Record
   settled actual cost once Azure billing catches up (24–48 h) and compare against the $0.79
   projection in `docs/reference/cost-model.md`.

**Acceptance.** A committed AKS evidence artifact with real, redacted measurements. Authenticated
smoke, HPA scale-up/down, and worker-kill recovery (exactly-once, lease reclaimed) all pass.
`make aks-verify-clean` passes, `az aks list` is empty, `az group exists -n fraudlens-aks-demo-rg`
is `false`. Actual session cost below $5.00. Ledger row shows `teardown verified = yes`.

> **Gates.** `make aks-up` is the first billable AKS action — approve explicitly and note the
> wall-clock start. `make aks-down` is destructive and irreversible — the evidence artifact must be
> validated and saved **before** destroying, or the session is wasted.

---

## Phase 6 — Reconcile the record

**What.** Update ADRs, claims, and runbooks to match what was actually done, then release.

**Why.** `docs/reference/claims.md` decides what wording your resume can defend. Leaving it at
`tested` after a real apply wastes the session.

**How.**

1. **Amend ADR-021** — dated amendment recording the SKU and priority change with its cause
   (`Standard DASv5 Family vCPUs = 0`, regional Spot = 3, no quota request would lift a zero family
   limit) and the addition of a `LoadBalancer` Service. Amend, do not rewrite.

2. **New ADR-029 — recurring operational budget.** ADR-028 covers one-time paid experiments against
   a $75 ceiling; the permanent URL is a recurring ~$2.50/month operational cost, which ADR-028's
   own "reconsider when" clause routes to a separate instrument. Record: scope, monthly ceiling, the
   two budget scopes, the hard caps and their values, the keep-warm window and its cost basis, the
   watchdog, the review cadence, and the decision that the permanent link runs on Container Apps
   while AKS stays ephemeral — with the ~50× cost ratio as the reason.

3. **Update `docs/reference/claims.md`:**

   | Claim | Before | After | Evidence |
   |---|---|---|---|
   | AKS Terraform validated / apply deferred | `tested` | **`demonstrated`** | `benchmarks/aks-hpa-scaling.md` |
   | Container Apps / Vercel / Supabase topology, deploy gates inert | `implemented` | **`demonstrated`** | live URL + `runbooks/azure-deploy.md` |
   | *(new)* Monthly cost projection with budget alerts at two scopes and enforced hard caps | — | **`implemented`** | `docs/reference/cost-model.md` |

   Update **only** to the level the captured artifacts support.

4. **Update runbooks.** `aks-deploy.md` — replace the "not applied" banner with the executed
   procedure, corrected SKU, and single-job workflow. `azure-deploy.md` — replace the "wired,
   validated, INERT" banner; document the Vercel→ACA path, secret-delivery ownership, stable FQDN,
   rollback, warm-window rationale, budget/cap behavior, and recovery.

5. **Update `plans/README.md`.** Move this plan into the active table. The three plans currently
   staged as deleted are not in the Retired table — add them with their removing commit, which is
   the established convention that pass was following.

6. **Leave the switches:** `AZURE_DEPLOY_ENABLED=true` and `VERCEL_DEPLOY_ENABLED=true`, both
   protected by required production approval; `AKS_DEPLOY_ENABLED=false` except during an
   explicitly approved evidence run.

**Files.** `docs/architecture/adr/ADR-021-*.md`, `docs/architecture/adr/ADR-029-*.md` (new),
`docs/architecture/adr/README.md`, `docs/reference/claims.md`,
`docs/runbooks/{aks,azure}-deploy.md`, `plans/README.md`.

**Tests.** `make docs` then `make docs-check` · `make docs-links-check` · `make skills-check` ·
`make pre-pr`

**Acceptance.** `make pre-pr` passes clean. Claims cite committed evidence for every `demonstrated`
row. No `tested` row survives where a `demonstrated` artifact now exists.

---

## Phase 7 — Publish release 0.4.0 on GitHub

**What.** Bump every version source, write the changelog, pass the release gate, merge to `main`,
tag `v0.4.0`, and let `release.yml` publish the GitHub Release.

**Why.** The work is not shipped until it is a tagged, published release. This is also the step
Golden Rules 1 and 2 bite hardest: **no autonomous tagging or pushing**, and **no AI attribution**
anywhere in the commits, tag, CHANGELOG, or generated release notes — GitHub builds its Contributors
sidebar from co-author trailers, and `git-cliff` generates the release body straight from commit
messages, so a bad trailer propagates into the published release.

**How.**

1. **Bump all seven version sources to `0.4.0`.** `scripts/release_gate.py` fails unless they agree
   exactly (rule 5 — one fact, not many copies):

   | Source | Path |
   |---|---|
   | root pyproject | `pyproject.toml` |
   | backend pyproject | `backend/pyproject.toml` |
   | fraudlens-core pyproject | `packages/fraudlens-core/pyproject.toml` |
   | fraudlens-llm pyproject | `packages/fraudlens-llm/pyproject.toml` |
   | fraudlens-ml pyproject | `packages/fraudlens-ml/pyproject.toml` |
   | frontend package.json | `frontend/package.json` |
   | backend `__version__` | `backend/src/fraudlens_backend/__init__.py` |

2. **Confirm the bump is correct.** `make version-next` derives the next SemVer from Conventional
   Commits since `v0.3.0`. This release adds features (Azure deployment, cost controls, budget
   alerts) without breaking the API, so expect **0.4.0**, a minor bump. If it proposes something
   else, the commit types are wrong — fix the commits, not the number.

3. **Write the CHANGELOG.** `make changelog-unreleased` renders the pending section via `git-cliff`
   to stdout. Add a `## [0.4.0] - 2026-XX-XX` section to `CHANGELOG.md` covering: Azure Container
   Apps deployment with the Vercel same-origin proxy; the AKS apply with measured HPA and durability
   evidence; cost projection, two budget scopes, and enforced hard caps; the VNet removal and its
   ~$22/month saving; the node SKU correction; and the CORS, secret-injection, lifecycle, LLM-budget,
   and log-quota fixes. `release_gate.py` fails if no section exists for the release version.

4. **Pass the automatable gate.** `make release-gate` asserts version consistency across all seven
   sources, the CHANGELOG section, and that the six required Make targets are wired. It is
   **propose-only and never tags**.

5. **Pass the four human-owned gate items**, which `release_gate.py` reports but can never
   auto-pass:
   - `make local-release-check` passes on a **clean checkout** (= `ci` + `tf-validate` +
     `docker-build` + `local-demo-smoke` + `release-gate`)
   - `make local-demo` boots the stack and prints the URL for browser UAT
   - full browser UAT, including model retrain → promote → rollback
   - **you** approve the `v0.4.0` tag and push (Golden Rule 1 — no autonomous tagging)

6. **Verify attribution is clean before tagging.** `make attribution-check` now scans **every
   local ref** (branches, remotes, tags), not just commits ahead of `main`, and
   `.githooks/commit-msg` rejects the trailer at commit time for any tool — both hardened
   2026-09-15, along with deleting the two backup refs that still held one. `make hooks-check`
   asserts the hook still rejects.
   Inspect the `git-cliff` output too — it becomes the published release body:
   ```bash
   make attribution-check
   git log --format='%an <%ae>%n%b' v0.3.0..HEAD | grep -iE 'co-authored-by|generated with|claude|anthropic' || echo "clean"
   ```
   A trailer that reaches `origin` must be stripped and history rewritten **before** the tag.

7. **Merge to `main`.** Open a PR from `release/0.4.0` → `main`, let CI go green, and merge. Rule 9:
   **a tag only ships from a CI-green commit.** Note the OIDC federated credentials cover
   `refs/heads/main` and `refs/heads/dev` directly, so post-merge deploys authenticate without
   relying on the `environment: production` subject.

8. **Tag and push — with your explicit permission, by you.** Tags carry the `v`; branches do not:
   ```bash
   git tag -a v0.4.0 -m "Release 0.4.0"
   git push origin v0.4.0
   ```

9. **`release.yml` publishes automatically.** On a `v*` tag it runs `verify` (the full
   `_ci-reusable.yml` gate again, at the tagged commit), derives the version as
   `${GITHUB_REF_NAME#v}`, generates the changelog with `orhun/git-cliff-action@v4`, and publishes
   via `softprops/action-gh-release@v2` with `contents: write`.

10. **Verify what was published:**
    ```bash
    gh run list -R Kartik-Hirijaganer/FraudLens --workflow=release -L 1
    gh release view v0.4.0 -R Kartik-Hirijaganer/FraudLens
    ```
    Confirm the release exists, is marked Latest, its notes contain no AI attribution, and the body
    matches the CHANGELOG section.

11. **Fix the known no-op while you are here.** `release.yml`'s "Version stamps" step only `echo`s
    the backend image tag and `VITE_APP_VERSION` — it wires nothing. Either connect it to the
    deploy path now that deploys are live, or delete it so it stops implying behavior it does not
    have.

12. **Operate after release.** Weekly for the first month: Azure MTD cost, replica state, revision
    failures, Log Analytics ingestion, OpenRouter spend. After seven days, compare observed daily
    cost with the projection — **if extrapolated Azure spend exceeds $10/month, switch to pure
    scale-to-zero rather than raising the budget.** Monthly: verify budget contacts, Infisical
    access, GHCR image visibility, and Vercel proxy health. After any secret rotation, redeploy
    secret references and confirm `/readyz` without logging values.

**Files.** The seven version sources, `CHANGELOG.md`, `.github/workflows/release.yml` (step 11).

**Tests.** `make version-next` · `make changelog-unreleased` · `make release-gate` ·
`make local-release-check` · `make attribution-check` · `gh release view v0.4.0`

**Acceptance.** `gh release view v0.4.0` shows a published GitHub Release marked Latest, generated
from a CI-green commit on `main`, with notes free of any AI attribution. All seven version sources
read `0.4.0`. The CHANGELOG has a `## [0.4.0]` section. The four human-owned gate items are
explicitly confirmed by you, not inferred.

> **Gates.** The commit, the merge, the tag, and the push each require your explicit permission
> (Golden Rule 1). `release_gate.py` names the tag approval as human-owned in `MANUAL_GATE_ITEMS`
> precisely so no agent can self-authorize it. I will prepare everything up to the tag and stop.

---

## Final acceptance matrix

| Requirement | Evidence |
|---|---|
| Azure deployment | Warm ACA revision serving the production portfolio URL |
| Azure AKS claim | Successful AKS run plus sanitized apply/deploy evidence |
| Terraform | Reviewed remote-state plan/apply, zero unmanaged drift |
| HPA | Time-series proof of 1 → ≥2 → 1 replicas, measured on AKS |
| Worker durability | Forced termination, lease recovery, exactly-once completion |
| Citation + hallucination gates | `make quality-gates` bound to the deployed SHA |
| Authentication | Real Supabase JWT; dev bypass proven inert in prod |
| Tenant isolation | Mismatched `agency_id` fails closed |
| Secrets | Infisical source, allowlisted delivery, no values in state/logs/artifacts |
| Live frontend | Vercel same-origin proxy preserves auth and streaming |
| Cost projection | `make azure-cost-plan` output dated and sourced from live prices |
| Budget protection | $25 budgets at two scopes, four notifications, enforced hard caps |
| Teardown | No AKS groups, resources, budget, or state entries remain |
| Published release | `gh release view v0.4.0` shows a Latest GitHub Release from a CI-green `main` commit |
| Attribution | `make attribution-check` clean; no AI trailer in commits, tag, CHANGELOG, or release notes |
| Personal ownership | Personal Git author, `github-personal` remote, personal GitHub repo/OIDC only |

---

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| VNet removal does not take effect (+$22/mo) | Medium | Phase 4's 48-hour cost gate explicitly checks for a Load Balancer meter |
| Workload-profile environment created instead of Consumption (+$73/mo) | Low | Same gate checks for an "Environment Management" meter |
| Measured cold start far exceeds 20–75 s (large image pull) | Medium | Phase 4 step 9 measures it. Remedies cheapest-first: widen the keep-warm window (+$0.30 per hour-per-weekday/mo), shrink the image, or `min_replicas = 1` (~$10.20/mo) |
| Infisical operator cannot authenticate via Azure workload identity | Medium | Highest-uncertainty step. Verify the `InfisicalSecret` reconciles and the Secret materializes **before** running the HPA demo |
| GHCR package is private → image pull fails | Medium | Explicit gate in Phase 4 step 1, before the apply |
| Cluster autoscaler cannot add the 2nd node (capacity, not quota) | Low | 4 of 72 regional vCPUs. If it happens, evidence still captures pod HPA within one node and says so |
| Forgotten AKS teardown burns ~$126/mo | Low | `if: always()` teardown, 4-hour deadline, daily watchdog (24 h detection), `aks-verify-clean`, ledger gate |
| GitHub cron drifts or is skipped | Medium | Best-effort by design. A missed ping costs one cold start, not money |
| AKS numbers differ from published kind numbers | High (expected) | Report AKS measurements as their own artifact; do not reconcile |
| `/readyz` 503 because one probe is `skipped` | Medium | The aggregate requires all five `ok`; check each probe individually during smoke, not just the status code |

---

## Explicitly out of scope

- The quality-gated SAR cascade (`SARQualityGate`, BF16 escalation, the 1,000-case rerun) — the
  second resume bullet, separate work, no dependency on this plan.
- Resume editing — you chose to leave the wording as-is and land the work fast.
- KEDA / scale-to-zero on Kubernetes — ADR-021 accepted `minReplicas: 1`.
- ACR — `acr_enabled = false`; images stay on public GHCR, which is free.
- Azure Key Vault — ADR-010 keeps secrets in Infisical; no Key Vault module by governance.
- Azure PostgreSQL — ADR-011 keeps Postgres on Supabase.
- `services_split_enabled` internal Container Apps split — scaffolded and inert.
- Azure quota increase requests — the chosen SKU fits existing quota (D2).

---

## Golden Rules observed

1. **No commit or push without explicit permission** — this plan proposes changes; it commits nothing.
2. **No AI attribution** — enforced by `make attribution-check`; no co-author trailer in any commit,
   PR, tag, or CHANGELOG entry.
3. **No secrets in source** — ACA secrets injected at deploy time from Infisical via OIDC; AKS
   secrets via the Infisical operator. Neither path touches tfvars or Terraform state.
4. **Plans in `plans/`** — filed on approval as
   `plans/2026-09-15-azure-deployment-cost-projection-and-budget-alerts.md`.
7. **No billable or mutating cloud action without permission** — gates at `cost-guardrails` apply,
   the `AZURE_DEPLOY_ENABLED` flip, `aks-up`, and `aks-down`; every mutating target requires
   `CONFIRM=yes`; every ephemeral resource has a paired teardown and a read-only clean verification.

**Identity.** `origin` is `git@github-personal:Kartik-Hirijaganer/FraudLens.git`, commits author as
`Kartik Hirijaganer <65550498+Kartik-Hirijaganer@users.noreply.github.com>`, `gh` active account is
`Kartik-Hirijaganer`, Azure is the personal subscription under `kartikhirijaganer@gmail.com`. Phase
0's identity guard enforces all four mechanically — **both** your personal and work GitHub accounts
are currently authenticated in `gh`, so this is a live hazard, not a theoretical one. No work
account is used anywhere in this plan.
