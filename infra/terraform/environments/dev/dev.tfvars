# Non-secret dev values (committed). Use with: terraform apply -var-file=dev.tfvars
# Account ids (subscription/tenant/client) are supplied via TF_VAR_* by the OIDC pipeline and
# are NOT committed here. container_image is stamped (build-once SHA tag) by the deploy job.
location               = "eastus"
name_prefix            = "fraudlens-dev"
environment            = "dev"
acr_enabled            = false # public GHCR image source (free); set true to provision ACR
storage_account_name   = "fraudlensdevsa"
services_split_enabled = false # v1 single external gateway app; internal split is inert (ADR-004)
min_replicas           = 0     # scale-to-zero (~$0 idle)
max_replicas           = 2
log_retention_days     = 30
blob_lifecycle_days    = 90
