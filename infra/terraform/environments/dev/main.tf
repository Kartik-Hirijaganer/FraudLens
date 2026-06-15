# Dev environment composition (plan §15.1) — a resource group wiring the modules into the v1
# single-external-gateway topology: networking + observability + blob + identity feed the
# `gateway_app` (the only external ingress). The internal `service_app` split is SCAFFOLDED but
# inert (`services_split_enabled = false` => zero instances; validated, not applied — ADR-004).
# ACR is optional (`acr_enabled = false` => public GHCR, no registry credential). Container Apps
# Jobs run the retrain cron + the on-demand batch scorer.

locals {
  tags = {
    project     = "FraudLens"
    environment = var.environment
    managed_by  = "terraform"
  }
  # Empty => the gateway pulls a public GHCR image anonymously; set only when acr_enabled.
  registry_server = var.acr_enabled ? module.acr[0].login_server : ""
  acr_id          = var.acr_enabled ? module.acr[0].id : ""
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

module "observability" {
  source              = "../../modules/observability"
  name_prefix         = var.name_prefix
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  log_retention_days  = var.log_retention_days
  tags                = local.tags
}

module "acr" {
  source              = "../../modules/acr"
  count               = var.acr_enabled ? 1 : 0
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
  lifecycle_days      = var.blob_lifecycle_days
  tags                = local.tags
}

module "identity" {
  source              = "../../modules/identity"
  name_prefix         = var.name_prefix
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  acr_id              = local.acr_id
  storage_account_id  = module.blob.storage_account_id
  tags                = local.tags
}

module "gateway_app" {
  source                         = "../../modules/gateway_app"
  name_prefix                    = var.name_prefix
  location                       = var.location
  resource_group_name            = azurerm_resource_group.this.name
  infrastructure_subnet_id       = module.networking.apps_subnet_id
  log_analytics_workspace_id     = module.observability.log_analytics_workspace_id
  identity_id                    = module.identity.id
  registry_server                = local.registry_server
  container_image                = var.container_image
  min_replicas                   = var.min_replicas
  max_replicas                   = var.max_replicas
  cors_allow_origins             = var.gateway_cors_origins
  app_insights_connection_string = module.observability.app_insights_connection_string
  tags                           = local.tags
}

# Internal service split — inert in v1 (zero instances). When services_split_enabled flips true,
# one internal-ingress Container App per service is created in the SAME environment (ADR-004).
module "service_app" {
  source                       = "../../modules/service_app"
  for_each                     = var.services_split_enabled ? toset(var.split_services) : toset([])
  name_prefix                  = var.name_prefix
  service_name                 = each.key
  resource_group_name          = azurerm_resource_group.this.name
  container_app_environment_id = module.gateway_app.environment_id
  identity_id                  = module.identity.id
  registry_server              = local.registry_server
  container_image              = var.container_image
  tags                         = local.tags
}

module "job_retrain" {
  source                       = "../../modules/jobs"
  name_prefix                  = var.name_prefix
  job_name                     = "retrain"
  location                     = var.location
  resource_group_name          = azurerm_resource_group.this.name
  container_app_environment_id = module.gateway_app.environment_id
  identity_id                  = module.identity.id
  registry_server              = local.registry_server
  container_image              = var.container_image
  command                      = ["python", "scripts/retrain.py"]
  trigger_type                 = "schedule"
  cron_expression              = var.retrain_cron
  tags                         = local.tags
}

module "job_batch_score" {
  source                       = "../../modules/jobs"
  name_prefix                  = var.name_prefix
  job_name                     = "batch-score"
  location                     = var.location
  resource_group_name          = azurerm_resource_group.this.name
  container_app_environment_id = module.gateway_app.environment_id
  identity_id                  = module.identity.id
  registry_server              = local.registry_server
  container_image              = var.container_image
  command                      = ["python", "-m", "fraudlens_backend.jobs.runner"]
  trigger_type                 = "manual"
  tags                         = local.tags
}
