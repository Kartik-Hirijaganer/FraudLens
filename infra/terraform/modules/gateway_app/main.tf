# Gateway app module (plan §4.3, §15.1, §15.7) — the SINGLE external entry point in v1.
# The gateway edge and the in-process service modules ship as one Container App with
# `ingress: external` and `allow_insecure_connections = false` (HTTPS only). It owns the
# shared Container Apps environment (linked to Log Analytics) that the internal `service_app`
# split reuses unchanged later (ADR-004). Probes are tuned for a heavy-ML cold start: a
# startup probe with a budget > the ≤75s cold-start target so the platform never kills a
# still-loading container, with liveness/readiness engaging only after startup succeeds.
# Revision mode is Multiple so a new revision is created at 0% traffic and promoted only
# after smoke passes (the deploy workflow shifts traffic; §15.7 promote-or-abort).

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

variable "log_analytics_workspace_id" {
  type        = string
  description = "Log Analytics workspace id the environment ships app logs to."
}

variable "identity_id" {
  type        = string
  description = "User-assigned managed identity id (registry pull + Blob + Infisical OIDC)."
}

variable "registry_server" {
  type        = string
  description = "Image registry login server. Empty => public GHCR (anonymous pull, no registry block)."
  default     = ""
}

variable "container_image" {
  type        = string
  description = "Fully-qualified backend image reference (registry/repo:tag)."
}

variable "cpu" {
  type        = number
  description = "vCPU per replica."
  default     = 0.5
}

variable "memory" {
  type        = string
  description = "Memory per replica (xgboost/shap/chromadb resident)."
  default     = "1Gi"
}

variable "min_replicas" {
  type        = number
  description = "Minimum replicas (0 => scale-to-zero, ~$0 idle)."
  default     = 0
}

variable "max_replicas" {
  type        = number
  description = "Maximum replicas."
  default     = 3
}

variable "cold_start_budget_seconds" {
  type        = number
  description = "Worst-case cold start (model + ChromaDB load). The startup probe budget must exceed it."
  default     = 75
}

variable "startup_probe_period_seconds" {
  type        = number
  description = "Seconds between startup-probe attempts."
  default     = 10
}

variable "cors_allow_origins" {
  type        = list(string)
  description = "Allowed CORS origins (the Vercel frontend), injected as FRAUDLENS_CORS_ALLOW_ORIGINS."
  default     = []
}

variable "app_insights_connection_string" {
  type        = string
  description = "Application Insights connection string injected for traces/APM."
  default     = ""
  sensitive   = true
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to all resources."
  default     = {}
}

locals {
  # failure_count_threshold * period must clear the cold-start budget so the platform never
  # kills a still-loading ML container. ceil(budget/period) + 2 attempts of headroom.
  startup_failure_threshold = ceil(var.cold_start_budget_seconds / var.startup_probe_period_seconds) + 2
}

resource "azurerm_container_app_environment" "this" {
  name                       = "${var.name_prefix}-env"
  location                   = var.location
  resource_group_name        = var.resource_group_name
  infrastructure_subnet_id   = var.infrastructure_subnet_id
  log_analytics_workspace_id = var.log_analytics_workspace_id
  tags                       = var.tags
}

resource "azurerm_container_app" "this" {
  name                         = "${var.name_prefix}-api"
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = var.resource_group_name
  # Multiple => a new revision lands at 0% traffic; the deploy workflow promotes it to 100%
  # only after smoke passes (and aborts by leaving the prior revision live). §15.7.
  revision_mode = "Multiple"
  tags          = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [var.identity_id]
  }

  # Only emitted when pulling from a private registry (ACR via managed identity). Public GHCR
  # needs no credentials, so the block is omitted entirely when registry_server is empty.
  dynamic "registry" {
    for_each = var.registry_server == "" ? [] : [var.registry_server]
    content {
      server   = registry.value
      identity = var.identity_id
    }
  }

  ingress {
    external_enabled           = true
    allow_insecure_connections = false # HTTPS only (plan §15.3 / Phase 13)
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
      name   = "fraudlens-backend"
      image  = var.container_image
      cpu    = var.cpu
      memory = var.memory

      env {
        name  = "FRAUDLENS_ENVIRONMENT"
        value = "prod"
      }
      env {
        name  = "FRAUDLENS_CORS_ALLOW_ORIGINS"
        value = join(",", var.cors_allow_origins)
      }
      dynamic "env" {
        for_each = var.app_insights_connection_string == "" ? [] : [var.app_insights_connection_string]
        content {
          name  = "APPLICATIONINSIGHTS_CONNECTION_STRING"
          value = env.value
        }
      }

      # Generous startup budget so a slow model/ChromaDB load is never killed mid-boot.
      startup_probe {
        transport               = "HTTP"
        port                    = 8000
        path                    = "/healthz"
        interval_seconds        = var.startup_probe_period_seconds
        failure_count_threshold = local.startup_failure_threshold
        timeout                 = 5
      }

      # Engages only after startup succeeds; /readyz gates traffic on DB+ChromaDB+model+Infisical.
      liveness_probe {
        transport        = "HTTP"
        port             = 8000
        path             = "/healthz"
        interval_seconds = 30
      }

      readiness_probe {
        transport        = "HTTP"
        port             = 8000
        path             = "/readyz"
        interval_seconds = 10
      }
    }
  }
}

output "environment_id" {
  description = "Container Apps environment id (shared with the internal service split)."
  value       = azurerm_container_app_environment.this.id
}

output "app_name" {
  description = "Name of the gateway container app."
  value       = azurerm_container_app.this.name
}

output "app_fqdn" {
  description = "Public FQDN of the gateway app ingress."
  value       = azurerm_container_app.this.latest_revision_fqdn
}

output "startup_probe_budget_seconds" {
  description = "Effective startup-probe budget (threshold * period); must exceed the cold start."
  value       = local.startup_failure_threshold * var.startup_probe_period_seconds
}
