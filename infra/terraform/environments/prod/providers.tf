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
  features {
    resource_group {
      # Azure AUTO-CREATES resources inside this group that no Terraform resource owns -- an
      # Application Insights component always spawns an "Application Insights Smart Detection"
      # action group beside itself. With the provider default, their presence refuses the resource
      # group deletion outright, so a location change or a teardown cannot complete: terraform
      # destroys everything it manages, then fails on the group and leaves the remainder stranded.
      # This root OWNS its resource group -- nothing else is deployed into it -- so deleting the
      # group means deleting exactly what this root created plus what Azure attached to it.
      prevent_deletion_if_contains_resources = false
    }
  }
  use_oidc        = true
  subscription_id = var.subscription_id
  tenant_id       = var.tenant_id
  client_id       = var.client_id
}
