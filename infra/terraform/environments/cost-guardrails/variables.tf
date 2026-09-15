# Inputs for the subscription-wide budget. Account ids and notification recipients arrive through
# TF_VAR_* from the OIDC pipeline and are never committed.

variable "subscription_id" {
  type        = string
  description = "Azure subscription the budget is scoped to."
  sensitive   = true
}

variable "tenant_id" {
  type        = string
  description = "Entra tenant id used by the azurerm provider."
  sensitive   = true
}

variable "client_id" {
  type        = string
  description = "OIDC federated client id used by the azurerm provider."
  sensitive   = true
}

variable "name_prefix" {
  type        = string
  description = "Prefix used for the subscription budget name."
  default     = "fraudlens-cost-guardrails"
}

variable "amount_usd" {
  type        = number
  description = "Monthly subscription-wide budget amount in USD."
  default     = 25
}

variable "budget_contact_emails" {
  type        = list(string)
  description = "Human-owned budget recipients supplied through TF_VAR_budget_contact_emails."
  sensitive   = true
}

variable "budget_start_date" {
  type        = string
  description = "First UTC day of the current budget month."
}
