# Networking module — VNet plus independently optional Container Apps and AKS subnets.

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

variable "aks_subnet_prefixes" {
  type        = list(string)
  description = "Address prefixes for the non-delegated AKS node subnet; empty disables it."
  default     = []
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
  count                = length(var.apps_subnet_prefixes) > 0 ? 1 : 0
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

moved {
  from = azurerm_subnet.apps
  to   = azurerm_subnet.apps[0]
}

resource "azurerm_subnet" "aks" {
  count                = length(var.aks_subnet_prefixes) > 0 ? 1 : 0
  name                 = "${var.name_prefix}-aks-subnet"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = var.aks_subnet_prefixes
}

resource "azurerm_network_security_group" "aks" {
  count               = length(var.aks_subnet_prefixes) > 0 ? 1 : 0
  name                = "${var.name_prefix}-aks-nsg"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_subnet_network_security_group_association" "aks" {
  count                     = length(var.aks_subnet_prefixes) > 0 ? 1 : 0
  subnet_id                 = azurerm_subnet.aks[0].id
  network_security_group_id = azurerm_network_security_group.aks[0].id
}

output "vnet_id" {
  description = "Resource id of the virtual network."
  value       = azurerm_virtual_network.this.id
}

output "apps_subnet_id" {
  description = "Resource id of the Container Apps infrastructure subnet."
  value       = try(azurerm_subnet.apps[0].id, null)
}

output "aks_subnet_id" {
  description = "Resource id of the AKS node subnet, or null when disabled."
  value       = try(azurerm_subnet.aks[0].id, null)
}
