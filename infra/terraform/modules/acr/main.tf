# ACR module — Azure Container Registry for the backend image.

variable "name" {
  type        = string
  description = "Globally-unique ACR name (alphanumeric, 5-50 chars)."
}

variable "resource_group_name" {
  type        = string
  description = "Resource group for the registry."
}

variable "location" {
  type        = string
  description = "Azure region."
}

variable "sku" {
  type        = string
  description = "Registry SKU (Basic/Standard/Premium)."
  default     = "Basic"
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to all resources."
  default     = {}
}

resource "azurerm_container_registry" "this" {
  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = var.sku
  admin_enabled       = false # pull via managed identity (AcrPull), never admin creds
  tags                = var.tags
}

output "id" {
  description = "Resource id of the registry."
  value       = azurerm_container_registry.this.id
}

output "login_server" {
  description = "Login server hostname of the registry."
  value       = azurerm_container_registry.this.login_server
}
