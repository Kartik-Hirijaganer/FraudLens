# Networking module — an optional VNet plus independently optional Container Apps and AKS
# subnets. A root that requests no subnet gets no network at all: a custom-network Container
# Apps environment provisions a Standard Load Balancer and public IP as fixed infrastructure
# (~$22/month), which the public-egress-only gateway has no need of (D1).

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

variable "aks_public_api_port" {
  type        = number
  description = "AKS backend port exposed through the public demonstration load balancer."
  default     = null
  nullable    = true

  validation {
    condition = (
      var.aks_public_api_port == null ||
      (var.aks_public_api_port >= 1 && var.aks_public_api_port <= 65535)
    )
    error_message = "aks_public_api_port must be null or a valid TCP port."
  }
}

variable "aks_health_probe_node_port" {
  type        = number
  description = "Fixed AKS health-check NodePort admitted only from AzureLoadBalancer."
  default     = null
  nullable    = true

  validation {
    condition = (
      var.aks_health_probe_node_port == null ||
      (var.aks_health_probe_node_port >= 30000 && var.aks_health_probe_node_port <= 32767)
    )
    error_message = "aks_health_probe_node_port must be null or within the Kubernetes NodePort range."
  }
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to all resources."
  default     = {}
}

locals {
  subnet_prefix_count = length(var.apps_subnet_prefixes) + length(var.aks_subnet_prefixes)
}

resource "azurerm_virtual_network" "this" {
  count               = local.subnet_prefix_count > 0 ? 1 : 0
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
  virtual_network_name = azurerm_virtual_network.this[0].name
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

moved {
  from = azurerm_virtual_network.this
  to   = azurerm_virtual_network.this[0]
}

resource "azurerm_subnet" "aks" {
  count                = length(var.aks_subnet_prefixes) > 0 ? 1 : 0
  name                 = "${var.name_prefix}-aks-subnet"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.this[0].name
  address_prefixes     = var.aks_subnet_prefixes
}

resource "azurerm_network_security_group" "aks" {
  count               = length(var.aks_subnet_prefixes) > 0 ? 1 : 0
  name                = "${var.name_prefix}-aks-nsg"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_network_security_rule" "aks_public_api" {
  count                       = length(var.aks_subnet_prefixes) > 0 && var.aks_public_api_port != null ? 1 : 0
  name                        = "AllowFraudLensPublicApi"
  priority                    = 100
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = tostring(var.aks_public_api_port)
  source_address_prefix       = "Internet"
  destination_address_prefix  = "*"
  resource_group_name         = var.resource_group_name
  network_security_group_name = azurerm_network_security_group.aks[0].name
}

resource "azurerm_network_security_rule" "aks_health_probe" {
  count                       = length(var.aks_subnet_prefixes) > 0 && var.aks_health_probe_node_port != null ? 1 : 0
  name                        = "AllowAzureLoadBalancerHealthProbe"
  priority                    = 110
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = tostring(var.aks_health_probe_node_port)
  source_address_prefix       = "AzureLoadBalancer"
  destination_address_prefix  = "*"
  resource_group_name         = var.resource_group_name
  network_security_group_name = azurerm_network_security_group.aks[0].name
}

resource "azurerm_subnet_network_security_group_association" "aks" {
  count                     = length(var.aks_subnet_prefixes) > 0 ? 1 : 0
  subnet_id                 = azurerm_subnet.aks[0].id
  network_security_group_id = azurerm_network_security_group.aks[0].id
}

output "vnet_id" {
  description = "Resource id of the virtual network, or null when no subnet was requested."
  value       = try(azurerm_virtual_network.this[0].id, null)
}

output "apps_subnet_id" {
  description = "Resource id of the Container Apps infrastructure subnet."
  value       = try(azurerm_subnet.apps[0].id, null)
}

output "aks_subnet_id" {
  description = "Resource id of the AKS node subnet, or null when disabled."
  value       = try(azurerm_subnet.aks[0].id, null)
}
