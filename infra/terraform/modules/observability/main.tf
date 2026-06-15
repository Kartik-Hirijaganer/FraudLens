# Observability module (plan §3.3, §11, §15.1) — Log Analytics workspace (app logs, capped
# retention to bound ingestion cost) + workspace-based Application Insights (traces/APM). The
# Container Apps environment links to this workspace so gateway/service/job logs land here; the
# App Insights connection string is injected into the gateway app for tracing. Audit data does
# NOT live here — it is in Postgres `audit_logs` (ADR-005); this is operational telemetry only.

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
  description = "Resource group for observability resources."
}

variable "log_retention_days" {
  type        = number
  description = "Log Analytics retention in days (capped to bound ingestion cost; ~30d, plan §11)."
  default     = 30
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to all resources."
  default     = {}
}

resource "azurerm_log_analytics_workspace" "this" {
  name                = "${var.name_prefix}-law"
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days
  tags                = var.tags
}

resource "azurerm_application_insights" "this" {
  name                = "${var.name_prefix}-ai"
  location            = var.location
  resource_group_name = var.resource_group_name
  workspace_id        = azurerm_log_analytics_workspace.this.id
  application_type    = "web"
  retention_in_days   = var.log_retention_days
  tags                = var.tags
}

output "log_analytics_workspace_id" {
  description = "Log Analytics workspace id the Container Apps environment ships logs to."
  value       = azurerm_log_analytics_workspace.this.id
}

output "app_insights_connection_string" {
  description = "Application Insights connection string (injected into the gateway for tracing)."
  value       = azurerm_application_insights.this.connection_string
  sensitive   = true
}
