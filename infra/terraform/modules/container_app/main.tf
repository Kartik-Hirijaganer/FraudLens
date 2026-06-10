# Container App module — the Container Apps environment + the FastAPI app, pulling
# its image from ACR via the user-assigned identity. Probes hit /healthz, /readyz.

variable "name_prefix" {
  type        = string
  description = "Prefix for resource names."
}

variable "location" {
  type        = string
  description = "Azure region."
}

variable "resource_group_name" {
  type        = string
  description = "Resource group for the app."
}

variable "infrastructure_subnet_id" {
  type        = string
  description = "Subnet id for the Container Apps environment."
}

variable "identity_id" {
  type        = string
  description = "User-assigned managed identity id (AcrPull)."
}

variable "acr_login_server" {
  type        = string
  description = "ACR login server used as the image registry."
}

variable "container_image" {
  type        = string
  description = "Fully-qualified backend image reference (registry/repo:tag)."
}

variable "cpu" {
  type        = number
  description = "vCPU per replica."
  default     = 0.25
}

variable "memory" {
  type        = string
  description = "Memory per replica."
  default     = "0.5Gi"
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to all resources."
  default     = {}
}

resource "azurerm_container_app_environment" "this" {
  name                     = "${var.name_prefix}-env"
  location                 = var.location
  resource_group_name      = var.resource_group_name
  infrastructure_subnet_id = var.infrastructure_subnet_id
  tags                     = var.tags
}

resource "azurerm_container_app" "this" {
  name                         = "${var.name_prefix}-api"
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [var.identity_id]
  }

  registry {
    server   = var.acr_login_server
    identity = var.identity_id
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    transport        = "auto"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 1
    max_replicas = 3

    container {
      name   = "fraudlens-backend"
      image  = var.container_image
      cpu    = var.cpu
      memory = var.memory

      liveness_probe {
        transport = "HTTP"
        port      = 8000
        path      = "/healthz"
      }

      readiness_probe {
        transport = "HTTP"
        port      = 8000
        path      = "/readyz"
      }
    }
  }
}

output "app_fqdn" {
  description = "Public FQDN of the container app ingress."
  value       = azurerm_container_app.this.latest_revision_fqdn
}
