output "resource_group" {
  description = "Ephemeral resource group destroyed after artifact export."
  value       = azurerm_resource_group.this.name
}

output "vm_name" {
  description = "Ephemeral batch virtual machine name."
  value       = module.batch_vm.vm_name
}

output "vm_size" {
  description = "Selected Azure CPU SKU."
  value       = module.batch_vm.vm_size
}

output "spot" {
  description = "Whether Spot priority is enabled."
  value       = module.batch_vm.spot
}

output "public_ip" {
  description = "Public IP restricted to operator SSH by the NSG."
  value       = module.batch_vm.public_ip
}

output "ssh_command" {
  description = "Command used by the operator to connect to the host."
  value       = module.batch_vm.ssh_command
}

output "storage_account_name" {
  description = "OAuth-only storage account used by transfer targets."
  value       = module.experiment_storage.storage_account_name
}

output "storage_container_name" {
  description = "Private experiment Blob container."
  value       = module.experiment_storage.container_name
}

output "container_url" {
  description = "OAuth-protected experiment container URL."
  value       = module.experiment_storage.container_url
}

output "budget_name" {
  description = "Subscription budget removed during teardown."
  value       = module.budget.name
}

output "watchdog_hours" {
  description = "In-VM hard deallocation deadline."
  value       = var.watchdog_hours
}

output "auto_shutdown_time" {
  description = "Platform shutdown backstop in UTC."
  value       = var.auto_shutdown_time
}
