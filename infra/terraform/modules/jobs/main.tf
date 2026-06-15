# Container Apps Job module (plan §3.5, §15.1) — scale-to-zero batch/MLOps work that runs the
# SAME image as the gateway with an overridden command, then exits (no standing worker/queue in
# v1, ADR / §2). One module call = one job; the environment wires a scheduled `retrain` (cron)
# and an on-demand `batch-score` (manual, started via `az containerapp job start`). Jobs get the
# user-assigned identity (Blob + Infisical OIDC), a replica timeout, and a retry limit so a
# transient failure re-runs without manual intervention.

variable "name_prefix" {
  type        = string
  description = "Prefix for resource names."
}

variable "job_name" {
  type        = string
  description = "Logical job name (e.g. retrain, batch-score)."
}

variable "location" {
  type        = string
  description = "Azure region."
}

variable "resource_group_name" {
  type        = string
  description = "Resource group for the job."
}

variable "container_app_environment_id" {
  type        = string
  description = "Shared Container Apps environment id."
}

variable "identity_id" {
  type        = string
  description = "User-assigned managed identity id (Blob + Infisical OIDC)."
}

variable "registry_server" {
  type        = string
  description = "Image registry login server. Empty => public GHCR (anonymous pull)."
  default     = ""
}

variable "container_image" {
  type        = string
  description = "Fully-qualified image reference (same artifact as the gateway)."
}

variable "command" {
  type        = list(string)
  description = "Entrypoint command for the job (e.g. [python, -m, fraudlens_backend.jobs.runner])."
}

variable "trigger_type" {
  type        = string
  description = "How the job fires: 'schedule' (cron) or 'manual' (on-demand)."
  default     = "manual"
  validation {
    condition     = contains(["schedule", "manual"], var.trigger_type)
    error_message = "trigger_type must be 'schedule' or 'manual'."
  }
}

variable "cron_expression" {
  type        = string
  description = "Cron schedule (used only when trigger_type = 'schedule')."
  default     = "0 3 * * 0" # weekly, Sunday 03:00 UTC
}

variable "replica_timeout_in_seconds" {
  type        = number
  description = "Max seconds a replica may run before it is stopped."
  default     = 1800
}

variable "replica_retry_limit" {
  type        = number
  description = "Retries on a failed replica (transient-failure resilience)."
  default     = 1
}

variable "cpu" {
  type        = number
  description = "vCPU per replica."
  default     = 1.0
}

variable "memory" {
  type        = string
  description = "Memory per replica (training/ingest are heavier than serving)."
  default     = "2Gi"
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to all resources."
  default     = {}
}

resource "azurerm_container_app_job" "this" {
  name                         = "${var.name_prefix}-${var.job_name}"
  location                     = var.location
  resource_group_name          = var.resource_group_name
  container_app_environment_id = var.container_app_environment_id
  replica_timeout_in_seconds   = var.replica_timeout_in_seconds
  replica_retry_limit          = var.replica_retry_limit
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

  dynamic "manual_trigger_config" {
    for_each = var.trigger_type == "manual" ? [1] : []
    content {
      parallelism              = 1
      replica_completion_count = 1
    }
  }

  dynamic "schedule_trigger_config" {
    for_each = var.trigger_type == "schedule" ? [1] : []
    content {
      cron_expression          = var.cron_expression
      parallelism              = 1
      replica_completion_count = 1
    }
  }

  template {
    container {
      name    = var.job_name
      image   = var.container_image
      cpu     = var.cpu
      memory  = var.memory
      command = var.command
    }
  }
}

output "job_name" {
  description = "Name of the Container Apps Job."
  value       = azurerm_container_app_job.this.name
}
