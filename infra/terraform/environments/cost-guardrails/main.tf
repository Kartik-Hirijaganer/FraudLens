# Subscription-wide cost guardrail (Phase 2). This root creates exactly ONE resource: an
# unfiltered monthly consumption budget. Every other Azure root in this repo is scoped to a named
# resource group, so a resource created outside those groups — by hand, by a mistyped root, or by
# a service that provisions its own group — bills silently. This budget is the only instrument
# that sees it. Budgets are free and non-billable; they alert, they do not cap. The caps that
# actually bind spend live in the workload roots (max_replicas, daily_quota_gb) and in
# config/prod.yaml (llm_daily_budget_usd).

module "budget" {
  source          = "../../modules/budget"
  name_prefix     = var.name_prefix
  subscription_id = var.subscription_id
  amount_usd      = var.amount_usd
  # Deliberately no resource_group_names: an unfiltered budget is the whole point of this root.
  contact_emails = var.budget_contact_emails
  start_date     = var.budget_start_date
}
