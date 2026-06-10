# Blob module — Storage account + a private container for artifacts.

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
  description = "Name of the private blob container."
  default     = "artifacts"
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

resource "azurerm_storage_container" "this" {
  name                  = var.container_name
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}

output "storage_account_id" {
  description = "Resource id of the storage account."
  value       = azurerm_storage_account.this.id
}

output "container_name" {
  description = "Name of the private container."
  value       = azurerm_storage_container.this.name
}
