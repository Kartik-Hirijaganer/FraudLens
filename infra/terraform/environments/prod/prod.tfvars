# Non-secret prod values (committed). Use with: terraform apply -var-file=prod.tfvars
# Account ids (subscription/tenant/client) come from TF_VAR_* via the OIDC pipeline.
# container_image is stamped (build-once SHA tag) by the deploy job; gateway_cors_origins is
# set to the Vercel frontend URL once the project exists.
location               = "eastus"
name_prefix            = "fraudlens-prod"
environment            = "prod"
acr_enabled            = false # public GHCR image source (free); set true to provision ACR
storage_account_name   = "fraudlensprodsa"
services_split_enabled = false # v1 single external gateway app; internal split is inert (ADR-004)
min_replicas           = 0     # scale-to-zero (~$0 idle)
max_replicas           = 5
log_retention_days     = 30
blob_lifecycle_days    = 365
