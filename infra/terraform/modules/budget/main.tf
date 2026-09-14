# Paid-experiment budget module (ADR-028) — a monthly subscription budget filtered to the
# ephemeral resource groups, with escalating actual-spend alerts and a forecast backstop.

variable "name_prefix" {
  type        = string
  description = "Prefix used for the subscription budget name."
}

variable "subscription_id" {
  type        = string
  description = "Azure subscription containing the filtered resource groups."
  sensitive   = true
}

variable "amount_usd" {
  type        = number
  description = "Monthly hard allocation represented by this alert."

  validation {
    condition     = var.amount_usd > 0
    error_message = "amount_usd must be positive."
  }
}

variable "resource_group_names" {
  type        = set(string)
  description = "Resource group names included in the budget filter."

  validation {
    condition     = length(var.resource_group_names) > 0
    error_message = "resource_group_names must not be empty."
  }
}

variable "contact_emails" {
  type        = list(string)
  description = "Human-owned budget notification addresses supplied at plan time."
  sensitive   = true

  validation {
    condition     = length(var.contact_emails) > 0
    error_message = "contact_emails must include at least one operator."
  }
}

variable "start_date" {
  type        = string
  description = "First UTC day of the current month in RFC3339 form."

  validation {
    condition     = can(regex("^[0-9]{4}-[0-9]{2}-01T00:00:00Z$", var.start_date))
    error_message = "start_date must be the first UTC day of a month."
  }
}

resource "azurerm_consumption_budget_subscription" "this" {
  name            = "${var.name_prefix}-budget"
  subscription_id = "/subscriptions/${var.subscription_id}"
  amount          = var.amount_usd
  time_grain      = "Monthly"

  time_period {
    start_date = var.start_date
  }

  filter {
    dimension {
      name     = "ResourceGroupName"
      operator = "In"
      values   = sort(tolist(var.resource_group_names))
    }
  }

  notification {
    enabled        = true
    threshold      = 50
    operator       = "GreaterThanOrEqualTo"
    threshold_type = "Actual"
    contact_emails = var.contact_emails
  }

  notification {
    enabled        = true
    threshold      = 80
    operator       = "GreaterThanOrEqualTo"
    threshold_type = "Actual"
    contact_emails = var.contact_emails
  }

  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThanOrEqualTo"
    threshold_type = "Actual"
    contact_emails = var.contact_emails
  }

  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThanOrEqualTo"
    threshold_type = "Forecasted"
    contact_emails = var.contact_emails
  }
}

output "name" {
  description = "Subscription budget name used by teardown verification."
  value       = azurerm_consumption_budget_subscription.this.name
}
