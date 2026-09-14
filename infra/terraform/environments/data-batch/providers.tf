# Provider pins for the ephemeral data-batch root (ADR-028). Local operations authenticate with
# the Azure CLI; CI/OIDC callers opt in with TF_VAR_use_oidc and account identifiers.

terraform {
  required_version = ">= 1.6"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

provider "azurerm" {
  features {}
  use_oidc            = var.use_oidc
  storage_use_azuread = true
  subscription_id     = var.subscription_id
  tenant_id           = var.tenant_id
  client_id           = var.client_id
}
