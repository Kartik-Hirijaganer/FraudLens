# Provider + version pins for the dev environment. Auth is GitHub→Azure OIDC
# (use_oidc = true) — NO client secret is stored. The subscription/tenant/client
# ids are non-secret identifiers supplied via TF_VAR_* at plan/apply time.

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
  use_oidc        = true
  subscription_id = var.subscription_id
  tenant_id       = var.tenant_id
  client_id       = var.client_id
}
