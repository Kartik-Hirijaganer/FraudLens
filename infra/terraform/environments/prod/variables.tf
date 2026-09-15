# Prod environment inputs. Account-specific ids come from TF_VAR_* (non-secret).
# Non-account knobs default here and may be overridden in prod.tfvars.

variable "subscription_id" {
  type        = string
  description = "Azure subscription id (TF_VAR_subscription_id)."
}

variable "tenant_id" {
  type        = string
  description = "Azure tenant id (TF_VAR_tenant_id)."
}

variable "client_id" {
  type        = string
  description = "OIDC federated app (client) id (TF_VAR_client_id)."
}

variable "location" {
  type        = string
  description = "Azure region."
  default     = "eastus"
}

variable "name_prefix" {
  type        = string
  description = "Prefix for resource names."
  default     = "fraudlens-prod"
}

variable "environment" {
  type        = string
  description = "Environment name (tag)."
  default     = "prod"
}

variable "vnet_address_space" {
  type        = list(string)
  description = "Address space for the VNet."
  default     = ["10.20.0.0/16"]
}

variable "apps_subnet_prefixes" {
  type        = list(string)
  description = "Container Apps subnet prefixes (>= /23); empty creates no VNet or subnet at all."
  default     = []
}

variable "acr_enabled" {
  type        = bool
  description = "Use ACR as the image registry; false => public GHCR (default, free)."
  default     = false
}

variable "acr_name" {
  type        = string
  description = "Globally-unique ACR name (used only when acr_enabled)."
  default     = "fraudlensprodacr"
}

variable "acr_sku" {
  type        = string
  description = "ACR SKU."
  default     = "Standard"
}

variable "storage_account_name" {
  type        = string
  description = "Globally-unique storage account name."
}

variable "container_image" {
  type        = string
  description = "Backend image reference to deploy (set by the release pipeline; GHCR by default)."
  default     = "mcr.microsoft.com/k8se/quickstart:latest"
}

variable "services_split_enabled" {
  type        = bool
  description = "Split services to internal-ingress apps (scale-up, ADR-004). False in v1 (inert)."
  default     = false
}

variable "split_services" {
  type        = list(string)
  description = "Service modules created when services_split_enabled (internal ingress)."
  default     = ["investigation", "scoring", "rag", "sar", "admin"]
}

variable "min_replicas" {
  type        = number
  description = "Gateway minimum replicas (0 => scale-to-zero, ~$0 idle)."
  default     = 0
}

variable "max_replicas" {
  type        = number
  description = "Gateway maximum replicas (a hard ceiling on concurrent billable instances)."
  default     = 1
}

variable "gateway_cors_origins" {
  type        = list(string)
  description = "Allowed CORS origins (the Vercel frontend URL)."
  default     = []
}

variable "log_retention_days" {
  type        = number
  description = "Log Analytics retention in days (cost control)."
  default     = 30
}

variable "blob_lifecycle_days" {
  type        = number
  description = "Days after which SAR PDFs expire (blob lifecycle, cost control)."
  default     = 365
}

variable "budget_amount_usd" {
  type        = number
  description = "Monthly budget alert amount covering the prod resource group."
  default     = 25
}

variable "budget_contact_emails" {
  type        = list(string)
  description = "Human-owned budget recipients supplied through TF_VAR_budget_contact_emails."
  sensitive   = true
}

variable "budget_start_date" {
  type        = string
  description = "First UTC day of the current budget month."
}
