# Non-secret prod values (committed). Use with: terraform apply -var-file=prod.tfvars
# Account ids (subscription/tenant/client) come from TF_VAR_* via the OIDC pipeline.
# container_image is stamped (build-once SHA tag) by the deploy job.
location               = "eastus"
name_prefix            = "fraudlens-prod"
environment            = "prod"
acr_enabled            = false # public GHCR image source (free); set true to provision ACR
storage_account_name   = "fraudlensprodsa"
services_split_enabled = false # v1 single external gateway app; internal split is inert (ADR-004)
min_replicas           = 0     # scale-to-zero (~$0 idle)
max_replicas           = 1     # hard ceiling: one replica cannot be exceeded on this budget (D12d)
apps_subnet_prefixes   = []    # no custom VNet: a platform-managed environment costs $0 (D1)
log_retention_days     = 30
blob_lifecycle_days    = 365
budget_amount_usd      = 25 # RG-scoped alert; the subscription-wide net is environments/cost-guardrails

# The browser reaches the API same-origin through the Vercel proxy (frontend/vercel.json), so
# CORS is not load-bearing. This is the defence-in-depth backstop behind it: safe to set now
# that FRAUDLENS_CORS_ALLOW_ORIGINS is jsonencode'd (D3) instead of comma-joined.
gateway_cors_origins = ["https://fraud-lens-amber.vercel.app"]
