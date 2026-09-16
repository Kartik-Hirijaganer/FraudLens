# AKS demonstration root (ADR-021) — applied only for the bounded release-0.4 evidence session.
# The permanent application remains on Container Apps; this root is always destroyed after proof.

locals {
  node_resource_group = "${var.name_prefix}-nodes-rg"
  tags = {
    project     = "FraudLens"
    environment = var.environment
    managed_by  = "terraform"
    release     = "0.4.0"
    lifecycle   = "ephemeral"
  }
  acr_id                     = var.acr_enabled ? module.acr[0].id : ""
  log_analytics_workspace_id = var.monitoring_enabled ? module.observability[0].log_analytics_workspace_id : ""
}

resource "azurerm_resource_group" "this" {
  name     = "${var.name_prefix}-rg"
  location = var.location
  tags     = local.tags
}

module "networking" {
  source               = "../../modules/networking"
  name_prefix          = var.name_prefix
  location             = var.location
  resource_group_name  = azurerm_resource_group.this.name
  vnet_address_space   = var.vnet_address_space
  apps_subnet_prefixes = []
  aks_subnet_prefixes  = var.aks_subnet_prefixes
  tags                 = local.tags
}

module "observability" {
  source              = "../../modules/observability"
  count               = var.monitoring_enabled ? 1 : 0
  name_prefix         = var.name_prefix
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  log_retention_days  = 30
  tags                = local.tags
}

module "acr" {
  source              = "../../modules/acr"
  count               = var.acr_enabled ? 1 : 0
  name                = var.acr_name
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  sku                 = "Basic"
  tags                = local.tags
}

module "aks" {
  source                     = "../../modules/aks"
  name_prefix                = var.name_prefix
  location                   = var.location
  resource_group_name        = azurerm_resource_group.this.name
  node_resource_group        = local.node_resource_group
  tenant_id                  = var.tenant_id
  subnet_id                  = module.networking.aks_subnet_id
  kubernetes_version         = var.kubernetes_version
  sku_tier                   = var.sku_tier
  system_vm_size             = var.system_vm_size
  user_pool_enabled          = var.user_pool_enabled
  user_vm_size               = var.user_vm_size
  user_min_count             = var.user_min_count
  user_max_count             = var.user_max_count
  cluster_admin_object_ids   = var.cluster_admin_object_ids
  authorized_ip_ranges       = var.authorized_ip_ranges
  acr_id                     = local.acr_id
  monitoring_enabled         = var.monitoring_enabled
  log_analytics_workspace_id = local.log_analytics_workspace_id
  tags                       = local.tags
}

module "budget" {
  source          = "../../modules/budget"
  name_prefix     = var.name_prefix
  subscription_id = var.subscription_id
  amount_usd      = var.budget_amount_usd
  resource_group_names = [
    azurerm_resource_group.this.name,
    local.node_resource_group,
  ]
  contact_emails = var.budget_contact_emails
  start_date     = var.budget_start_date
}
