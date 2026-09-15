# Provider pins for the future AKS demonstration apply. Local plans use Azure CLI authentication;
# the inert workflow opts into the existing GitHub-to-Azure OIDC federation.

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
  use_oidc                        = var.use_oidc
  resource_provider_registrations = "none"
  subscription_id                 = var.subscription_id
  tenant_id                       = var.tenant_id
  client_id                       = var.client_id
}
