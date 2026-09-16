# Monthly subscription budget module (ADR-028) with escalating actual-spend alerts and a forecast
# backstop. It serves BOTH governed scopes from one implementation: pass resource group names to
# filter the budget to those groups (the ephemeral paid-experiment scope), or pass none to leave
# the budget subscription-wide — the only scope that catches spend in an unplanned resource group.

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
  description = "Resource groups to filter on; empty leaves the budget subscription-wide."
  default     = []

  validation {
    condition     = alltrue([for name in var.resource_group_names : length(trimspace(name)) > 0])
    error_message = "resource_group_names must not contain a blank name."
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

  # No names => no filter block => the budget covers the whole subscription.
  dynamic "filter" {
    for_each = length(var.resource_group_names) > 0 ? [1] : []

    content {
      dimension {
        name     = "ResourceGroupName"
        operator = "In"
        values   = sort(tolist(var.resource_group_names))
      }
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
