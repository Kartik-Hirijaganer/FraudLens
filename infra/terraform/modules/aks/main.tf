# Ephemeral AKS demonstration module (ADR-021) — Entra-only access, OIDC/workload identity,
# Azure CNI Overlay with Cilium policy, a critical system pool, and a bounded Spot user pool.

variable "name_prefix" {
  type        = string
  description = "Prefix for the AKS cluster and managed node resource group."
}

variable "location" {
  type        = string
  description = "Azure region for the AKS cluster."
}

variable "resource_group_name" {
  type        = string
  description = "Resource group containing the managed AKS control-plane resource."
}

variable "node_resource_group" {
  type        = string
  description = "Explicit resource group Azure creates for cluster-managed node resources."
}

variable "tenant_id" {
  type        = string
  description = "Microsoft Entra tenant used by managed AKS authentication."
  sensitive   = true
}

variable "subnet_id" {
  type        = string
  description = "Non-delegated subnet used by the system and user node pools."
}

variable "kubernetes_version" {
  type        = string
  description = "Optional AKS Kubernetes version; null selects Azure's supported default."
  default     = null
  nullable    = true
}

variable "sku_tier" {
  type        = string
  description = "AKS control-plane pricing tier."
  default     = "Free"

  validation {
    condition     = contains(["Free", "Standard"], var.sku_tier)
    error_message = "sku_tier must be Free or Standard."
  }
}

variable "system_vm_size" {
  type        = string
  description = "VM size for the single critical system node."
  default     = "Standard_B2s"
}

variable "user_pool_enabled" {
  type        = bool
  description = "Create the bounded Spot user pool used by application workloads."
  default     = true
}

variable "user_vm_size" {
  type        = string
  description = "VM size for Spot application nodes."
  default     = "Standard_D2as_v5"
}

variable "user_min_count" {
  type        = number
  description = "Minimum Spot user nodes while the cluster is running."
  default     = 1

  validation {
    condition     = var.user_min_count >= 1
    error_message = "user_min_count must retain at least one application node."
  }
}

variable "user_max_count" {
  type        = number
  description = "Maximum Spot user nodes admitted by the node-pool autoscaler."
  default     = 2

  validation {
    condition     = var.user_max_count >= var.user_min_count && var.user_max_count <= 2
    error_message = "user_max_count must be between user_min_count and the release cap of 2."
  }
}

variable "cluster_admin_object_ids" {
  type        = list(string)
  description = "Entra object ids granted Azure Kubernetes Service RBAC Cluster Admin."
  sensitive   = true

  validation {
    condition     = length(var.cluster_admin_object_ids) > 0
    error_message = "At least one Entra cluster administrator is required."
  }
}

variable "authorized_ip_ranges" {
  type        = list(string)
  description = "Optional operator CIDRs admitted to the public AKS API endpoint."
  default     = []
}

variable "acr_id" {
  type        = string
  description = "Optional ACR resource id granted to the cluster kubelet identity."
  default     = ""
}

variable "monitoring_enabled" {
  type        = bool
  description = "Enable Container Insights only when its continuing cost is approved."
  default     = false
}

variable "log_analytics_workspace_id" {
  type        = string
  description = "Workspace id used only when monitoring_enabled is true."
  default     = ""
}

variable "service_cidr" {
  type        = string
  description = "ClusterIP service CIDR, separate from the VNet and pod CIDRs."
  default     = "10.60.0.0/16"
}

variable "dns_service_ip" {
  type        = string
  description = "CoreDNS address inside service_cidr."
  default     = "10.60.0.10"
}

variable "pod_cidr" {
  type        = string
  description = "Azure CNI Overlay pod CIDR."
  default     = "10.61.0.0/16"
}

variable "tags" {
  type        = map(string)
  description = "Governance tags applied to managed resources."
  default     = {}
}

resource "azurerm_kubernetes_cluster" "this" {
  name                = "${var.name_prefix}-aks"
  location            = var.location
  resource_group_name = var.resource_group_name
  node_resource_group = var.node_resource_group
  dns_prefix          = "${var.name_prefix}-aks"
  kubernetes_version  = var.kubernetes_version
  sku_tier            = var.sku_tier
  support_plan        = "KubernetesOfficial"

  role_based_access_control_enabled = true
  local_account_disabled            = true
  oidc_issuer_enabled               = true
  workload_identity_enabled         = true
  run_command_enabled               = false
  azure_policy_enabled              = true
  automatic_upgrade_channel         = "patch"
  node_os_upgrade_channel           = "NodeImage"

  default_node_pool {
    name                         = "system"
    vm_size                      = var.system_vm_size
    node_count                   = 1
    only_critical_addons_enabled = var.user_pool_enabled
    vnet_subnet_id               = var.subnet_id
    os_disk_type                 = "Managed"
    os_disk_size_gb              = 64
    os_sku                       = "Ubuntu"
    node_public_ip_enabled       = false
    max_pods                     = 50

    upgrade_settings {
      max_surge = "1"
    }
  }

  identity {
    type = "SystemAssigned"
  }

  azure_active_directory_role_based_access_control {
    tenant_id              = var.tenant_id
    azure_rbac_enabled     = true
    admin_group_object_ids = []
  }

  api_server_access_profile {
    authorized_ip_ranges = var.authorized_ip_ranges
  }

  network_profile {
    network_plugin      = "azure"
    network_plugin_mode = "overlay"
    network_data_plane  = "cilium"
    network_policy      = "cilium"
    load_balancer_sku   = "standard"
    outbound_type       = "loadBalancer"
    service_cidr        = var.service_cidr
    dns_service_ip      = var.dns_service_ip
    pod_cidr            = var.pod_cidr
  }

  dynamic "oms_agent" {
    for_each = var.monitoring_enabled ? [1] : []
    content {
      log_analytics_workspace_id      = var.log_analytics_workspace_id
      msi_auth_for_monitoring_enabled = true
    }
  }

  tags = var.tags

  lifecycle {
    precondition {
      condition     = length(var.authorized_ip_ranges) > 0
      error_message = "The public AKS API endpoint requires at least one authorized operator CIDR."
    }

    precondition {
      condition     = !var.monitoring_enabled || var.log_analytics_workspace_id != ""
      error_message = "monitoring_enabled requires a Log Analytics workspace id."
    }
  }
}

resource "azurerm_kubernetes_cluster_node_pool" "user" {
  count                  = var.user_pool_enabled ? 1 : 0
  name                   = "user"
  kubernetes_cluster_id  = azurerm_kubernetes_cluster.this.id
  vm_size                = var.user_vm_size
  mode                   = "User"
  priority               = "Spot"
  eviction_policy        = "Delete"
  spot_max_price         = -1
  auto_scaling_enabled   = true
  min_count              = var.user_min_count
  max_count              = var.user_max_count
  vnet_subnet_id         = var.subnet_id
  os_disk_type           = "Managed"
  os_disk_size_gb        = 64
  os_sku                 = "Ubuntu"
  node_public_ip_enabled = false
  max_pods               = 50
  node_labels = {
    "fraudlens.io/workload" = "application"
  }
  node_taints = ["kubernetes.azure.com/scalesetpriority=spot:NoSchedule"]

  tags = var.tags
}

resource "azurerm_role_assignment" "cluster_admin" {
  count                = length(nonsensitive(var.cluster_admin_object_ids))
  scope                = azurerm_kubernetes_cluster.this.id
  role_definition_name = "Azure Kubernetes Service RBAC Cluster Admin"
  principal_id         = var.cluster_admin_object_ids[count.index]
}

resource "azurerm_role_assignment" "acr_pull" {
  count                = var.acr_id == "" ? 0 : 1
  scope                = var.acr_id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_kubernetes_cluster.this.kubelet_identity[0].object_id
}

output "id" {
  description = "AKS cluster resource id."
  value       = azurerm_kubernetes_cluster.this.id
}

output "name" {
  description = "AKS cluster name."
  value       = azurerm_kubernetes_cluster.this.name
}

output "node_resource_group" {
  description = "Azure-managed node resource-group name."
  value       = azurerm_kubernetes_cluster.this.node_resource_group
}

output "oidc_issuer_url" {
  description = "OIDC issuer used by AKS workload identities."
  value       = azurerm_kubernetes_cluster.this.oidc_issuer_url
}

output "kubelet_identity_object_id" {
  description = "Kubelet identity object id used by the Infisical operator allowlist."
  value       = azurerm_kubernetes_cluster.this.kubelet_identity[0].object_id
}

output "kubelet_identity_client_id" {
  description = "Kubelet identity client id selected by the Infisical operator pod."
  value       = azurerm_kubernetes_cluster.this.kubelet_identity[0].client_id
}
