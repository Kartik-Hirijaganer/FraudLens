# Networking module — VNet + a subnet delegated to Azure Container Apps.

variable "name_prefix" {
  type        = string
  description = "Prefix for resource names."
}

variable "location" {
  type        = string
  description = "Azure region."
}

variable "resource_group_name" {
  type        = string
  description = "Resource group that hosts the network."
}

variable "vnet_address_space" {
  type        = list(string)
  description = "Address space for the virtual network."
}

variable "apps_subnet_prefixes" {
  type        = list(string)
  description = "Address prefixes for the Container Apps infrastructure subnet (>= /23)."
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to all resources."
  default     = {}
}

resource "azurerm_virtual_network" "this" {
  name                = "${var.name_prefix}-vnet"
  location            = var.location
  resource_group_name = var.resource_group_name
  address_space       = var.vnet_address_space
  tags                = var.tags
}

resource "azurerm_subnet" "apps" {
  name                 = "${var.name_prefix}-apps-subnet"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = var.apps_subnet_prefixes

  delegation {
    name = "containerapps"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

output "vnet_id" {
  description = "Resource id of the virtual network."
  value       = azurerm_virtual_network.this.id
}

output "apps_subnet_id" {
  description = "Resource id of the Container Apps infrastructure subnet."
  value       = azurerm_subnet.apps.id
}
