# Blob module (plan §15.3) — Storage account + private containers for model artifacts and SAR
# PDFs, with a lifecycle management policy: cool-tier aging to bound storage cost, plus expiry of
# SAR PDFs after `lifecycle_days`. Model artifacts age to cool but are NOT auto-deleted (they back
# the model registry pointer). TLS 1.2 minimum; no public blob access.

variable "name" {
  type        = string
  description = "Globally-unique storage account name (lowercase alphanumeric, 3-24 chars)."
}

variable "resource_group_name" {
  type        = string
  description = "Resource group for the storage account."
}

variable "location" {
  type        = string
  description = "Azure region."
}

variable "account_tier" {
  type        = string
  description = "Storage account tier."
  default     = "Standard"
}

variable "account_replication_type" {
  type        = string
  description = "Replication strategy."
  default     = "LRS"
}

variable "container_name" {
  type        = string
  description = "Name of the private model-artifacts container."
  default     = "artifacts"
}

variable "sar_pdf_container_name" {
  type        = string
  description = "Name of the private SAR-PDF container."
  default     = "sar-pdfs"
}

variable "lifecycle_days" {
  type        = number
  description = "Days after which SAR PDFs are deleted; all blobs tier to cool earlier (cost control)."
  default     = 90
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to all resources."
  default     = {}
}

resource "azurerm_storage_account" "this" {
  name                            = var.name
  resource_group_name             = var.resource_group_name
  location                        = var.location
  account_tier                    = var.account_tier
  account_replication_type        = var.account_replication_type
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  tags                            = var.tags
}

resource "azurerm_storage_container" "artifacts" {
  name                  = var.container_name
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}

resource "azurerm_storage_container" "sar_pdfs" {
  name                  = var.sar_pdf_container_name
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}

resource "azurerm_storage_management_policy" "this" {
  storage_account_id = azurerm_storage_account.this.id

  # All blobs age to the cool tier after 30 days of no modification (cheaper at-rest cost).
  rule {
    name    = "tier-to-cool"
    enabled = true
    filters {
      blob_types = ["blockBlob"]
    }
    actions {
      base_blob {
        tier_to_cool_after_days_since_modification_greater_than = 30
      }
    }
  }

  # SAR PDFs additionally expire after `lifecycle_days`; model artifacts are retained (registry).
  rule {
    name    = "expire-sar-pdfs"
    enabled = true
    filters {
      blob_types   = ["blockBlob"]
      prefix_match = ["${var.sar_pdf_container_name}/"]
    }
    actions {
      base_blob {
        delete_after_days_since_modification_greater_than = var.lifecycle_days
      }
    }
  }
}

output "storage_account_id" {
  description = "Resource id of the storage account."
  value       = azurerm_storage_account.this.id
}

output "container_name" {
  description = "Name of the private model-artifacts container."
  value       = azurerm_storage_container.artifacts.name
}

output "sar_pdf_container_name" {
  description = "Name of the private SAR-PDF container."
  value       = azurerm_storage_container.sar_pdfs.name
}
