# Azure data-batch experiment root (ADR-028) — one isolated PAYG CPU VM, private OAuth-only
# storage, and a subscription budget. Every resource is ephemeral and destroyed as one session.

locals {
  tags = {
    project     = "FraudLens"
    environment = var.environment
    managed_by  = "terraform"
    release     = "0.3.0"
    lifecycle   = "ephemeral"
    run_id      = var.run_id
  }
}

resource "azurerm_resource_group" "this" {
  name     = "${var.name_prefix}-rg"
  location = var.location
  tags     = local.tags
}

module "batch_vm" {
  source                       = "../../modules/batch_vm"
  name_prefix                  = var.name_prefix
  resource_group_name          = azurerm_resource_group.this.name
  location                     = var.location
  vnet_address_space           = var.vnet_address_space
  subnet_address_prefixes      = var.subnet_address_prefixes
  operator_cidr                = var.operator_cidr
  admin_username               = var.admin_username
  ssh_public_key               = var.ssh_public_key
  vm_size                      = var.vm_size
  gpu_enabled                  = false
  spot_enabled                 = var.spot_enabled
  eviction_policy              = "Deallocate"
  max_bid_price                = var.max_bid_price
  os_disk_size_gb              = var.os_disk_size_gb
  os_disk_storage_account_type = "Premium_LRS"
  auto_shutdown_time           = var.auto_shutdown_time
  watchdog_hours               = var.watchdog_hours
  grant_self_deallocate        = true
  tags                         = local.tags
}

module "experiment_storage" {
  source              = "../../modules/experiment_storage"
  name                = var.storage_account_name
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  container_name      = "experiments"
  retention_days      = var.storage_retention_days
  principals = [
    {
      id   = module.batch_vm.identity_principal_id
      type = "ServicePrincipal"
    },
    {
      id   = var.operator_principal_id
      type = var.operator_principal_type
    },
  ]
  allowed_subnet_ids = [module.batch_vm.subnet_id]
  operator_ip_rules  = [split("/", var.operator_cidr)[0]]
  tags               = local.tags
}

module "budget" {
  source               = "../../modules/budget"
  name_prefix          = var.name_prefix
  subscription_id      = var.subscription_id
  amount_usd           = var.budget_amount_usd
  resource_group_names = [azurerm_resource_group.this.name]
  contact_emails       = var.budget_contact_emails
  start_date           = var.budget_start_date
}
