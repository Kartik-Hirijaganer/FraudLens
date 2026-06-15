# Internal service app module (plan §4.3, §15.1, ADR-004) — the scale-up path that splits
# the in-process service modules out behind the gateway as their OWN Container Apps with
# `ingress: internal` (never publicly addressable; reachable only from the gateway over the
# environment's private network). It is SCAFFOLDED + `terraform validate`-checked but NOT
# applied in v1: the environments wire it with `count = var.services_split_enabled ? 1 : 0`,
# and `services_split_enabled` is false in v1, so the single external gateway app serves
# everything. Deploying it later needs no code rewrite — the gateway routes by internal DNS.

variable "name_prefix" {
  type        = string
  description = "Prefix for resource names."
}

variable "service_name" {
  type        = string
  description = "Logical service name (e.g. investigation, scoring, rag, sar, admin)."
}

variable "resource_group_name" {
  type        = string
  description = "Resource group for the app."
}

variable "container_app_environment_id" {
  type        = string
  description = "Shared Container Apps environment id (the same one the gateway owns)."
}

variable "identity_id" {
  type        = string
  description = "User-assigned managed identity id (registry pull + Blob + Infisical OIDC)."
}

variable "registry_server" {
  type        = string
  description = "Image registry login server. Empty => public GHCR (anonymous pull)."
  default     = ""
}

variable "container_image" {
  type        = string
  description = "Fully-qualified service image reference (registry/repo:tag)."
}

variable "cpu" {
  type        = number
  description = "vCPU per replica."
  default     = 0.5
}

variable "memory" {
  type        = string
  description = "Memory per replica."
  default     = "1Gi"
}

variable "min_replicas" {
  type        = number
  description = "Minimum replicas (0 => scale-to-zero)."
  default     = 0
}

variable "max_replicas" {
  type        = number
  description = "Maximum replicas."
  default     = 3
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to all resources."
  default     = {}
}

resource "azurerm_container_app" "this" {
  name                         = "${var.name_prefix}-${var.service_name}"
  container_app_environment_id = var.container_app_environment_id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Multiple"
  tags                         = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [var.identity_id]
  }

  dynamic "registry" {
    for_each = var.registry_server == "" ? [] : [var.registry_server]
    content {
      server   = registry.value
      identity = var.identity_id
    }
  }

  # INTERNAL only — never externally reachable. Traffic comes from the gateway over the
  # environment's private network. This is the trust-boundary invariant for the split.
  ingress {
    external_enabled           = false
    allow_insecure_connections = false
    target_port                = 8000
    transport                  = "auto"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    container {
      name   = "fraudlens-${var.service_name}"
      image  = var.container_image
      cpu    = var.cpu
      memory = var.memory

      readiness_probe {
        transport        = "HTTP"
        port             = 8000
        path             = "/readyz"
        interval_seconds = 10
      }
    }
  }
}

output "app_name" {
  description = "Name of the internal service container app."
  value       = azurerm_container_app.this.name
}

output "internal_fqdn" {
  description = "Internal FQDN the gateway routes to (not publicly resolvable)."
  value       = azurerm_container_app.this.latest_revision_fqdn
}
