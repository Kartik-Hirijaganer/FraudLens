# Identity module — user-assigned managed identity for the Container App, with
# least-privilege role assignments (pull from ACR, read/write the blob container).

variable "name_prefix" {
  type        = string
  description = "Prefix for resource names."
}

variable "resource_group_name" {
  type        = string
  description = "Resource group for the identity."
}

variable "location" {
  type        = string
  description = "Azure region."
}

variable "acr_id" {
  type        = string
  description = "Resource id of the ACR to grant AcrPull on. Empty => GHCR default (no AcrPull)."
  default     = ""
}

variable "storage_account_id" {
  type        = string
  description = "Resource id of the storage account to grant blob access on."
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to all resources."
  default     = {}
}

resource "azurerm_user_assigned_identity" "this" {
  name                = "${var.name_prefix}-id"
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = var.tags
}

# Only granted when ACR is the registry (acr_enabled). Public GHCR needs no pull credential.
resource "azurerm_role_assignment" "acr_pull" {
  count                = var.acr_id == "" ? 0 : 1
  scope                = var.acr_id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.this.principal_id
}

resource "azurerm_role_assignment" "blob_contributor" {
  scope                = var.storage_account_id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.this.principal_id
}

output "id" {
  description = "Resource id of the user-assigned identity."
  value       = azurerm_user_assigned_identity.this.id
}

output "principal_id" {
  description = "Principal (object) id of the identity."
  value       = azurerm_user_assigned_identity.this.principal_id
}

output "client_id" {
  description = "Client id of the identity."
  value       = azurerm_user_assigned_identity.this.client_id
}
