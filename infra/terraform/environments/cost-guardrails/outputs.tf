# Outputs for the subscription-wide budget root.

output "budget_name" {
  description = "Subscription budget name used by read-only verification."
  value       = module.budget.name
}
