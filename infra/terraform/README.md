# Infrastructure (Terraform / Azure)

Scaffolded, **CI-validated, and NOT applied.** These configs are linted
(`terraform fmt -check`) and validated (`terraform init -backend=false && terraform validate`)
in CI, but **no `terraform apply` runs** (Golden Rule 1).

**Bootstrap status (2026-09-13):** the Azure account, the remote-state backend, and the
GitHub→Azure OIDC federation now **exist** (see below). Deploy remains **inert by choice** —
`AZURE_DEPLOY_ENABLED` is `false`, so `deploy-backend.yml` / `deploy-frontend.yml` skip every
Azure job. Flipping that one repo variable to `true` is the only step left to go live.
A verified `terraform plan` against the real subscription reports **15 to add, 0 to change,
0 to destroy** for `dev`; nothing has been applied.

## Layout

```
infra/terraform/
├── modules/
│   ├── networking/      # VNet + Container Apps-delegated subnet
│   ├── observability/   # Log Analytics workspace + Application Insights (capped retention)
│   ├── identity/        # user-assigned MI (Blob; AcrPull only when acr_enabled)
│   ├── acr/             # OPTIONAL registry (acr_enabled=false => public GHCR, default)
│   ├── blob/            # storage account + artifacts/sar-pdfs containers + lifecycle policy
│   ├── gateway_app/     # the v1 single EXTERNAL Container App (allowInsecure=false, tuned probes,
│   │                    #   Multiple revision mode for 0%-staged deploys); owns the shared env
│   ├── service_app/     # INTERNAL-ingress app — scaffolded + validated, NOT applied in v1
│   ├── jobs/            # Container Apps Job (retrain cron + on-demand batch-score)
│   ├── batch_vm/        # isolated CPU/GPU experiment VM + shutdown watchdogs
│   ├── experiment_storage/ # OAuth-only private Blob + lifecycle + scoped RBAC
│   ├── aks/             # Entra/OIDC AKS + Free control plane + bounded Spot user pool
│   └── budget/          # RG-filtered subscription budget alerts
├── environments/{dev,prod}/                                # application roots
    ├── providers.tf            # azurerm ~> 4, use_oidc = true (no client secret)
    ├── variables.tf            # inputs (account ids via TF_VAR_*; acr/split/retention knobs)
    ├── main.tf                 # resource group + module wiring
    ├── outputs.tf              # rg, registry login server, identity client id, app FQDN, probe budget
    ├── <env>.tfvars            # NON-SECRET knobs (committed)
    ├── backend.tf.template     # remote-state config (rename to backend.tf when ready)
    └── .terraform.lock.hcl     # provider lock (committed for reproducibility)
├── environments/data-batch/                             # ephemeral full-data CPU experiment
    ├── data-batch.tfvars       # West US 3, E16ads v5 PAYG, $15/8-hour bounds
    ├── backend.tf.template     # isolated data-batch state key
    └── providers/main/variables/outputs.tf
└── environments/aks-demo/                               # validate-only in release 0.3
    ├── aks-demo.tfvars       # Free/B2s + Spot D2as_v5 1–2; $15 bound
    ├── backend.tf.template   # isolated next-release AKS state key
    └── providers/main/variables/outputs.tf
```

The full deploy procedure (state bootstrap, OIDC, enabling gates, switch paths, fast/reliable
deploy flow) lives in [`docs/runbooks/azure-deploy.md`](../../docs/runbooks/azure-deploy.md).

## CI validation (what runs today)

```
make tf-validate # discovers dev, prod, data-batch, and aks-demo roots
make iac-scan    # Checkov on both release experiment roots, with documented exceptions
```

`-backend=false` skips backend init, so validation needs no Azure account or state storage.

## Ephemeral data-batch host

The Phase 6 prerequisite is implemented in `environments/data-batch`: one West US 3
`Standard_E16ads_v5` PAYG VM, isolated SSH-only network, OAuth-only experiment storage, managed
identity, a resource-group-filtered $15 budget, and two independent eight-hour shutdown controls.
`make data-batch-plan` is read-only; every create, upload, restart, and destroy target requires an
explicit `CONFIRM=yes`. See the [data-batch runbook](../../docs/runbooks/data-batch.md) for the
approval, pilot-admission, artifact, and clean-teardown sequence.

## Ephemeral AKS demonstration

`environments/aks-demo` composes an isolated VNet/subnet/NSG, the hardened AKS module, and an
RG-filtered budget. `make aks-plan` is the release 0.3 boundary: it prints the compute-only hourly
estimate and plans without refresh or apply. The actual cluster creation and measured AKS evidence
are deferred to a separately approved next release. See the
[AKS runbook](../../docs/runbooks/aks-deploy.md) and
[ADR-021](../../docs/architecture/adr/ADR-021-aks-ephemeral-kubernetes-demonstration.md).

## State backend bootstrap (out-of-band, one time) — ✅ DONE 2026-09-13

State storage was created **out of band** (not managed by this config, to avoid a
chicken-and-egg). What now exists:

| Resource | Value |
|---|---|
| Resource group | `fraudlens-tfstate-rg` (eastus) |
| Storage account | `fraudlenstfstate` — Standard_LRS, TLS1_2 min, **public blob access disabled**, **versioning on** |
| Container | `tfstate` (keys `dev.terraform.tfstate`, `prod.terraform.tfstate`) |

Reproduce (already applied):
```bash
az group create -n fraudlens-tfstate-rg -l eastus
az storage account create -n fraudlenstfstate -g fraudlens-tfstate-rg -l eastus \
  --sku Standard_LRS --min-tls-version TLS1_2 --allow-blob-public-access false --kind StorageV2
az storage account blob-service-properties update -n fraudlenstfstate \
  -g fraudlens-tfstate-rg --enable-versioning true
az storage container create -n tfstate --account-name fraudlenstfstate
```

**`backend.tf` is generated, not renamed.** `backend.tf.template` stays the committed
source of truth; `deploy-backend.yml` does `cp backend.tf.template backend.tf` before
`init`, and the generated `backend.tf` is gitignored. Locally, do the same:

```bash
cp backend.tf.template backend.tf && terraform init
```

## GitHub → Azure OIDC (no stored secrets) — ✅ DONE 2026-09-13

The deploy pipeline authenticates to Azure with a **federated credential** — there is no
client secret anywhere in GitHub or the repo. What now exists:

| Item | Value |
|---|---|
| Entra app / SP | `fraudlens-github-oidc` |
| Roles (subscription scope) | `Contributor` + `User Access Administrator` |
| Federated subjects | `…:environment:production`, `…:environment:Production`, `…:ref:refs/heads/dev`, `…:ref:refs/heads/main` |

`User Access Administrator` is required because the `identity` module creates role
assignments (`acr_pull`, `blob_contributor`). Both roles are subscription-scoped because
Terraform creates the resource groups themselves — tighten to RG scope later if desired.

Both `production` casings are registered: the workflows write `environment: production`
while the GitHub environment is stored as `Production`, and the OIDC `sub` claim must
match exactly.

The workflows set `permissions: id-token: write` and use `azure/login@v2` with
`client-id` / `tenant-id` / `subscription-id` (non-secret ids, stored as **repo
variables**) — Terraform's `provider "azurerm" { use_oidc = true }` then needs no secret.

## Infisical and `TF_VAR_*` mapping

- **Account identifiers** (subscription/tenant/client id) are **non-secret** and are
  supplied as `TF_VAR_subscription_id`, `TF_VAR_tenant_id`, `TF_VAR_client_id` at
  plan/apply time (from the OIDC login step / repo variables).
- **Application secrets** (DB passwords, JWT keys, third-party API keys) are **never**
  Terraform inputs. They are fetched at **runtime from Infisical** by the app and injected
  as Container App env/secret refs — keeping secrets out of Terraform state entirely.

## Apply order (once live)

`environments/<env>`: `init` → `plan -var-file=<env>.tfvars` → `apply`. The resource group and
modules resolve in dependency order (networking/observability/blob (+acr if enabled) → identity →
gateway_app → jobs; service_app stays inert until `services_split_enabled = true`). Deploy is driven
by `.github/workflows/deploy-backend.yml` (build-once → GHCR → revision @0% → gated migration →
smoke → promote-or-abort); see [`docs/runbooks/azure-deploy.md`](../../docs/runbooks/azure-deploy.md)
and [`docs/runbooks/deploy-rollback.md`](../../docs/runbooks/deploy-rollback.md).
