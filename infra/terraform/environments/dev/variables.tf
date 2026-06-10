# Dev environment inputs. Account-specific ids come from TF_VAR_* (non-secret,
# uninitialized until the Azure account exists). Everything else has a sane default
# and may be overridden in dev.tfvars (non-secret only).

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
  default     = "fraudlens-dev"
}

variable "environment" {
  type        = string
  description = "Environment name (tag)."
  default     = "dev"
}

variable "vnet_address_space" {
  type        = list(string)
  description = "Address space for the VNet."
  default     = ["10.10.0.0/16"]
}

variable "apps_subnet_prefixes" {
  type        = list(string)
  description = "Container Apps subnet prefixes (>= /23)."
  default     = ["10.10.0.0/23"]
}

variable "acr_name" {
  type        = string
  description = "Globally-unique ACR name."
}

variable "acr_sku" {
  type        = string
  description = "ACR SKU."
  default     = "Basic"
}

variable "storage_account_name" {
  type        = string
  description = "Globally-unique storage account name."
}

variable "container_image" {
  type        = string
  description = "Backend image reference to deploy (set by the release pipeline)."
  default     = "mcr.microsoft.com/k8se/quickstart:latest"
}
