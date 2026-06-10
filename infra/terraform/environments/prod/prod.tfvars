# Non-secret prod values (committed). Use with: terraform apply -var-file=prod.tfvars
# Account ids (subscription/tenant/client) come from TF_VAR_* via the OIDC pipeline.
location             = "eastus"
name_prefix          = "fraudlens-prod"
environment          = "prod"
acr_name             = "fraudlensprodacr"
acr_sku              = "Standard"
storage_account_name = "fraudlensprodsa"
