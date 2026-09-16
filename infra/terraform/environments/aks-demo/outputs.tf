output "resource_group" {
  description = "Resource group containing the AKS control-plane resource."
  value       = azurerm_resource_group.this.name
}

output "node_resource_group" {
  description = "Azure-managed node resource group included in budget and cleanup checks."
  value       = module.aks.node_resource_group
}

output "cluster_name" {
  description = "AKS cluster name used by credential and lifecycle commands."
  value       = module.aks.name
}

output "cluster_id" {
  description = "AKS cluster resource id."
  value       = module.aks.id
}

output "kubelet_identity_object_id" {
  description = "Kubelet object id allowlisted for the Infisical operator identity."
  value       = module.aks.kubelet_identity_object_id
}

output "kubelet_identity_client_id" {
  description = "Kubelet client id configured on the Infisical operator authentication."
  value       = module.aks.kubelet_identity_client_id
}

output "oidc_issuer_url" {
  description = "AKS workload-identity issuer URL."
  value       = module.aks.oidc_issuer_url
}

output "budget_name" {
  description = "Subscription budget removed with the ephemeral cluster."
  value       = module.budget.name
}
