output "resource_group" {
  description = "Resource group name."
  value       = azurerm_resource_group.this.name
}

output "acr_login_server" {
  description = "ACR login server (image registry)."
  value       = module.acr.login_server
}

output "identity_client_id" {
  description = "Client id of the app's managed identity."
  value       = module.identity.client_id
}

output "app_fqdn" {
  description = "Public FQDN of the deployed backend."
  value       = module.container_app.app_fqdn
}
