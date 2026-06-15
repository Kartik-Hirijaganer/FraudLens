output "resource_group" {
  description = "Resource group name."
  value       = azurerm_resource_group.this.name
}

output "registry_login_server" {
  description = "Image registry login server (empty => public GHCR)."
  value       = local.registry_server
}

output "identity_client_id" {
  description = "Client id of the app's managed identity."
  value       = module.identity.client_id
}

output "app_fqdn" {
  description = "Public FQDN of the deployed gateway app."
  value       = module.gateway_app.app_fqdn
}

output "startup_probe_budget_seconds" {
  description = "Gateway startup-probe budget; must exceed the cold-start target (≤75s)."
  value       = module.gateway_app.startup_probe_budget_seconds
}
