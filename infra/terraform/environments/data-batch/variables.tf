# Inputs for the ephemeral data-batch root. Account identifiers, operator network/key material,
# budget contacts, and time-derived safeguards arrive through TF_VAR_* or the guarded Make target.

variable "subscription_id" {
  type        = string
  description = "Azure subscription id supplied at plan time."
  sensitive   = true

  validation {
    condition     = can(regex("^[0-9a-fA-F-]{36}$", var.subscription_id))
    error_message = "subscription_id must be a UUID."
  }
}

variable "tenant_id" {
  type        = string
  description = "Azure tenant id supplied at plan time."
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
  description = "Use workload OIDC instead of local Azure CLI authentication."
  default     = false
}

variable "location" {
  type        = string
  description = "Approved Azure experiment region."
  default     = "westus3"

  validation {
    condition     = var.location == "westus3"
    error_message = "Release 0.3 data-batch execution is approved only for West US 3."
  }
}

variable "name_prefix" {
  type        = string
  description = "Stable prefix for all data-batch resources."
  default     = "fraudlens-data-batch"
}

variable "environment" {
  type        = string
  description = "Governance environment tag."
  default     = "data-batch"
}

variable "run_id" {
  type        = string
  description = "Ledger-compatible resource-session identifier."
  default     = "data-batch-pending"

  validation {
    condition     = can(regex("^data-batch-[a-z0-9-]+$", var.run_id))
    error_message = "run_id must start with data-batch- and contain lowercase letters, digits, or hyphens."
  }
}

variable "vnet_address_space" {
  type        = list(string)
  description = "Address space for the isolated experiment network."
  default     = ["10.40.0.0/16"]
}

variable "subnet_address_prefixes" {
  type        = list(string)
  description = "Address prefixes for the experiment host subnet."
  default     = ["10.40.1.0/24"]
}

variable "operator_cidr" {
  type        = string
  description = "Current operator public CIDR; resolved at plan time and never committed."
  sensitive   = true
}

variable "operator_principal_id" {
  type        = string
  description = "Signed-in operator object id for container-scoped OAuth data access."
  sensitive   = true
}

variable "operator_principal_type" {
  type        = string
  description = "Azure AD object type for the signed-in operator principal."
  default     = "User"

  validation {
    condition     = contains(["ServicePrincipal", "User"], var.operator_principal_type)
    error_message = "operator_principal_type must be ServicePrincipal or User."
  }
}

variable "admin_username" {
  type        = string
  description = "Non-root SSH account for the experiment host."
  default     = "fraudlens"
}

variable "ssh_public_key" {
  type        = string
  description = "Operator SSH public key; resolved at plan time and never committed."
  sensitive   = true
}

variable "vm_size" {
  type        = string
  description = "CPU SKU selected for the full-data experiment."
  default     = "Standard_E16ads_v5"
}

variable "spot_enabled" {
  type        = bool
  description = "Whether to use Spot; false while regional Spot quota is below 16 vCPUs."
  default     = false
}

variable "max_bid_price" {
  type        = number
  description = "Maximum Spot price when Spot is explicitly enabled."
  default     = -1
}

variable "os_disk_size_gb" {
  type        = number
  description = "Persistent Premium OS/checkpoint disk size."
  default     = 128
}

variable "auto_shutdown_time" {
  type        = string
  description = "UTC platform auto-shutdown time, calculated as plan time plus eight hours."
}

variable "watchdog_hours" {
  type        = number
  description = "Independent in-VM deallocation deadline."
  default     = 8
}

variable "storage_account_name" {
  type        = string
  description = "Globally unique, non-secret experiment storage account name."
}

variable "storage_retention_days" {
  type        = number
  description = "Lifecycle expiry for uploaded inputs, checkpoints, and evidence."
  default     = 30
}

variable "budget_amount_usd" {
  type        = number
  description = "Azure budget alert amount for the CPU allocation."
  default     = 15
}

variable "budget_contact_emails" {
  type        = list(string)
  description = "Human budget contacts resolved from the signed-in account or TF_VAR override."
  sensitive   = true
}

variable "budget_start_date" {
  type        = string
  description = "First UTC day of the current month, calculated by the Make target."
}
