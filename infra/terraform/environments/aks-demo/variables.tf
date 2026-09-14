# Inputs for the validate-only AKS demonstration root. Account and operator identifiers arrive
# through TF_VAR_* at plan time; committed tfvars contain non-secret architecture choices only.

variable "subscription_id" {
  type        = string
  description = "Azure subscription id supplied through TF_VAR_subscription_id."
  sensitive   = true

  validation {
    condition     = can(regex("^[0-9a-fA-F-]{36}$", var.subscription_id))
    error_message = "subscription_id must be a UUID."
  }
}

variable "tenant_id" {
  type        = string
  description = "Microsoft Entra tenant id supplied through TF_VAR_tenant_id."
  sensitive   = true
}

variable "client_id" {
  type        = string
  description = "OIDC application client id; null for local Azure CLI authentication."
  sensitive   = true
  nullable    = true
  default     = null
}

variable "use_oidc" {
  type        = bool
  description = "Use GitHub Actions OIDC rather than the local Azure CLI session."
  default     = false
}

variable "location" {
  type        = string
  description = "Azure region for the future human-approved AKS demonstration."
  default     = "westus3"
}

variable "name_prefix" {
  type        = string
  description = "Stable prefix for AKS demonstration resources."
  default     = "fraudlens-aks-demo"
}

variable "environment" {
  type        = string
  description = "Governance environment tag."
  default     = "aks-demo"
}

variable "vnet_address_space" {
  type        = list(string)
  description = "Address space for the isolated AKS demonstration VNet."
  default     = ["10.50.0.0/16"]
}

variable "aks_subnet_prefixes" {
  type        = list(string)
  description = "Non-delegated subnet used by AKS node pools."
  default     = ["10.50.0.0/23"]
}

variable "kubernetes_version" {
  type        = string
  description = "Optional AKS version; null lets Azure select a currently supported default."
  nullable    = true
  default     = null
}

variable "sku_tier" {
  type        = string
  description = "AKS control-plane tier."
  default     = "Free"
}

variable "system_vm_size" {
  type        = string
  description = "System node VM size."
  default     = "Standard_B2s"
}

variable "user_pool_enabled" {
  type        = bool
  description = "Create the bounded Spot application node pool."
  default     = true
}

variable "user_vm_size" {
  type        = string
  description = "Application node VM size."
  default     = "Standard_D2as_v5"
}

variable "user_min_count" {
  type        = number
  description = "Minimum application nodes while AKS is running."
  default     = 1
}

variable "user_max_count" {
  type        = number
  description = "Maximum application nodes while AKS is running."
  default     = 2
}

variable "cluster_admin_object_ids" {
  type        = list(string)
  description = "Entra object ids granted AKS RBAC Cluster Admin."
  sensitive   = true
}

variable "authorized_ip_ranges" {
  type        = list(string)
  description = "Operator CIDRs admitted to the public AKS API endpoint."
  sensitive   = true
  default     = []
}

variable "acr_enabled" {
  type        = bool
  description = "Create an ACR for immutable deployment images."
  default     = false
}

variable "acr_name" {
  type        = string
  description = "Globally unique ACR name used only when acr_enabled is true."
  default     = "fraudlensaksdemoacr"
}

variable "monitoring_enabled" {
  type        = bool
  description = "Enable paid Container Insights only after a human cost decision."
  default     = false
}

variable "budget_amount_usd" {
  type        = number
  description = "Monthly budget alert amount covering cluster and node resource groups."
  default     = 15
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
