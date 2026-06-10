# Dev environment composition — a resource group wiring the five modules together.

locals {
  tags = {
    project     = "FraudLens"
    environment = var.environment
    managed_by  = "terraform"
  }
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
  apps_subnet_prefixes = var.apps_subnet_prefixes
  tags                 = local.tags
}

module "acr" {
  source              = "../../modules/acr"
  name                = var.acr_name
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  sku                 = var.acr_sku
  tags                = local.tags
}

module "blob" {
  source              = "../../modules/blob"
  name                = var.storage_account_name
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  tags                = local.tags
}

module "identity" {
  source              = "../../modules/identity"
  name_prefix         = var.name_prefix
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  acr_id              = module.acr.id
  storage_account_id  = module.blob.storage_account_id
  tags                = local.tags
}

module "container_app" {
  source                   = "../../modules/container_app"
  name_prefix              = var.name_prefix
  location                 = var.location
  resource_group_name      = azurerm_resource_group.this.name
  infrastructure_subnet_id = module.networking.apps_subnet_id
  identity_id              = module.identity.id
  acr_login_server         = module.acr.login_server
  container_image          = var.container_image
  tags                     = local.tags
}
