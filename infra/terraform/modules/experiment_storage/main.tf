# Ephemeral experiment storage (ADR-028) — OAuth-only private Blob storage with network
# restrictions, least-privilege container RBAC, version recovery, and deterministic expiry.

variable "name" {
  type        = string
  description = "Globally unique lowercase storage account name."

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.name))
    error_message = "name must contain 3-24 lowercase alphanumeric characters."
  }
}

variable "resource_group_name" {
  type        = string
  description = "Resource group for the experiment storage account."
}

variable "location" {
  type        = string
  description = "Azure region for experiment storage."
}

variable "container_name" {
  type        = string
  description = "Private container holding input, checkpoints, and exported evidence."
  default     = "experiments"
}

variable "retention_days" {
  type        = number
  description = "Maximum age of experiment blobs before lifecycle deletion."
  default     = 30

  validation {
    condition     = var.retention_days >= 1 && var.retention_days <= 90
    error_message = "retention_days must be between 1 and 90."
  }
}

variable "principals" {
  type = list(object({
    id   = string
    type = string
  }))
  description = "Principals and Azure AD object types granted container-scoped Blob access."
  default     = []

  validation {
    condition = alltrue([
      for principal in var.principals : contains(["ServicePrincipal", "User"], principal.type)
    ])
    error_message = "principal type must be ServicePrincipal or User."
  }
}

variable "allowed_subnet_ids" {
  type        = set(string)
  description = "Subnets permitted through the storage firewall."
  default     = []
}

variable "operator_ip_rules" {
  type        = set(string)
  description = "Operator public IPv4 addresses or CIDRs permitted for upload and download."
  sensitive   = true
  default     = []
}

variable "tags" {
  type        = map(string)
  description = "Governance tags applied to the storage account."
  default     = {}
}

resource "azurerm_storage_account" "this" {
  #checkov:skip=CKV_AZURE_33:The account is Blob-only and creates no Queue workload; Blob access is bounded by RBAC, firewall, and session teardown.
  #checkov:skip=CKV_AZURE_59:Public routing is needed for the approved operator upload, but the firewall defaults to deny, container access is private, and shared keys are disabled.
  #checkov:skip=CKV_AZURE_206:LRS is intentional for short-lived reproducible synthetic inputs; cross-region replication adds cost without durability value before teardown.
  #checkov:skip=CKV2_AZURE_33:A service endpoint plus deny-by-default firewall isolates the ephemeral account without the cost and DNS surface of a private endpoint.
  #checkov:skip=CKV2_AZURE_1:Synthetic public data needs no customer-managed key; Microsoft-managed encryption plus infrastructure encryption protects the short-lived copy.
  #checkov:skip=CKV_AZURE_36:No trusted-service bypass is needed; only the exact operator IP and experiment subnet may reach the OAuth-only account.
  name                              = var.name
  resource_group_name               = var.resource_group_name
  location                          = var.location
  account_tier                      = "Standard"
  account_replication_type          = "LRS"
  account_kind                      = "StorageV2"
  min_tls_version                   = "TLS1_2"
  https_traffic_only_enabled        = true
  allow_nested_items_to_be_public   = false
  shared_access_key_enabled         = false
  default_to_oauth_authentication   = true
  cross_tenant_replication_enabled  = false
  infrastructure_encryption_enabled = true
  public_network_access_enabled     = true
  local_user_enabled                = false
  tags                              = var.tags

  blob_properties {
    versioning_enabled = true

    delete_retention_policy {
      days = 7
    }

    container_delete_retention_policy {
      days = 7
    }
  }

  network_rules {
    default_action             = "Deny"
    bypass                     = ["None"]
    ip_rules                   = var.operator_ip_rules
    virtual_network_subnet_ids = var.allowed_subnet_ids
  }
}

resource "azurerm_storage_container" "this" {
  #checkov:skip=CKV2_AZURE_21:A separate logging workspace would outlive/add cost to this bounded session; run/checkpoint manifests and the resource ledger provide audit evidence.
  name                  = var.container_name
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}

resource "azurerm_storage_management_policy" "this" {
  storage_account_id = azurerm_storage_account.this.id

  rule {
    name    = "expire-experiment-data"
    enabled = true

    filters {
      blob_types   = ["blockBlob"]
      prefix_match = ["${var.container_name}/"]
    }

    actions {
      base_blob {
        delete_after_days_since_modification_greater_than = var.retention_days
      }
      snapshot {
        delete_after_days_since_creation_greater_than = var.retention_days
      }
      version {
        delete_after_days_since_creation = var.retention_days
      }
    }
  }
}

resource "azurerm_role_assignment" "blob_contributor" {
  count                            = length(var.principals)
  scope                            = azurerm_storage_container.this.id
  role_definition_name             = "Storage Blob Data Contributor"
  principal_id                     = var.principals[count.index].id
  principal_type                   = var.principals[count.index].type
  skip_service_principal_aad_check = var.principals[count.index].type == "ServicePrincipal"
}

output "storage_account_id" {
  description = "Resource id of the ephemeral storage account."
  value       = azurerm_storage_account.this.id
}

output "storage_account_name" {
  description = "Name used by operator upload and download commands."
  value       = azurerm_storage_account.this.name
}

output "container_name" {
  description = "Private experiment container name."
  value       = azurerm_storage_container.this.name
}

output "container_url" {
  description = "OAuth-protected Blob container URL."
  value       = "${azurerm_storage_account.this.primary_blob_endpoint}${azurerm_storage_container.this.name}"
}
