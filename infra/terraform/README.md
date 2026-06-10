# Infrastructure (Terraform / Azure)

Scaffolded, **CI-validated, and NOT applied.** The Azure account does not exist yet, so
these configs are linted (`terraform fmt -check`) and validated
(`terraform init -backend=false && terraform validate`) in CI, but **no `terraform apply`
runs** until the account and the remote-state backend are bootstrapped (Golden Rule 1).

## Layout

```
infra/terraform/
├── modules/{networking,identity,acr,blob,container_app}/   # reusable building blocks
└── environments/{dev,prod}/                                # one root module per env
    ├── providers.tf            # azurerm ~> 4, use_oidc = true (no client secret)
    ├── variables.tf            # inputs (account ids via TF_VAR_*)
    ├── main.tf                 # resource group + module wiring
    ├── outputs.tf              # rg, acr login server, identity client id, app FQDN
    ├── <env>.tfvars            # NON-SECRET knobs (committed)
    ├── backend.tf.template     # remote-state config (rename to backend.tf when ready)
    └── .terraform.lock.hcl     # provider lock (committed for reproducibility)
```

## CI validation (what runs today)

```
terraform -chdir=environments/dev  fmt -check -recursive
terraform -chdir=environments/dev  init -backend=false
terraform -chdir=environments/dev  validate
# …and the same for environments/prod
```

`-backend=false` skips backend init, so validation needs no Azure account or state storage.

## State backend bootstrap (out-of-band, one time)

1. Create the state storage **out of band** (not managed by this config to avoid a
   chicken-and-egg): a resource group + storage account + `tfstate` container, e.g.
   ```
   az group create -n fraudlens-tfstate-rg -l eastus
   az storage account create -n fraudlenstfstate -g fraudlens-tfstate-rg -l eastus --sku Standard_LRS --min-tls-version TLS1_2
   az storage container create -n tfstate --account-name fraudlenstfstate
   ```
2. In each environment, **rename `backend.tf.template` → `backend.tf`** (keys are
   per-env: `dev.terraform.tfstate`, `prod.terraform.tfstate`).
3. `terraform -chdir=environments/<env> init` (now configures the azurerm backend).

## GitHub → Azure OIDC (no stored secrets)

The deploy pipeline authenticates to Azure with a **federated credential** (no client
secret in GitHub):

1. Create an Entra app + service principal; grant it Contributor on the subscription
   (and User Access Administrator if it must create role assignments).
2. Add a **federated credential** trusting this repo's GitHub OIDC token (subject e.g.
   `repo:Kartik-Hirijaganer/FraudLens:ref:refs/heads/main` and the `production`
   environment). `azuread_application_federated_identity_credential` can manage this later.
3. The workflows set `permissions: id-token: write` and use `azure/login@v2` with
   `client-id` / `tenant-id` / `subscription-id` (non-secret ids) — Terraform's
   `provider "azurerm" { use_oidc = true }` then needs no secret.

## Infisical and `TF_VAR_*` mapping

- **Account identifiers** (subscription/tenant/client id) are **non-secret** and are
  supplied as `TF_VAR_subscription_id`, `TF_VAR_tenant_id`, `TF_VAR_client_id` at
  plan/apply time (from the OIDC login step / repo variables).
- **Application secrets** (DB passwords, JWT keys, third-party API keys) are **never**
  Terraform inputs. They are fetched at **runtime from Infisical** by the app and injected
  as Container App env/secret refs — keeping secrets out of Terraform state entirely.

## Apply order (once live)

`environments/<env>`: `init` → `plan -var-file=<env>.tfvars` → `apply`. The resource group
and modules resolve in dependency order (networking/acr/blob → identity → container_app).
Deploy is driven by `.github/workflows/deploy-backend.yml` (ACA staged revision → smoke →
promote); see [`docs/runbooks/deploy-rollback.md`](../../docs/runbooks/deploy-rollback.md).
