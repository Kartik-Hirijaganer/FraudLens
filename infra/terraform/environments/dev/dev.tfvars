# Non-secret dev values (committed). Use with: terraform apply -var-file=dev.tfvars
# Account ids (subscription/tenant/client) are supplied via TF_VAR_* by the OIDC
# pipeline and are NOT committed here. container_image is stamped by the release job.
location             = "eastus"
name_prefix          = "fraudlens-dev"
environment          = "dev"
acr_name             = "fraudlensdevacr"
acr_sku              = "Basic"
storage_account_name = "fraudlensdevsa"
