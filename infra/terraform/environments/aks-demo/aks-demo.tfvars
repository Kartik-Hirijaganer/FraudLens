# Validate-only AKS demonstration configuration. Account ids, Entra administrators, operator CIDRs,
# budget recipients, and the current budget month are supplied via TF_VAR_* and never committed.

location            = "westus3"
name_prefix         = "fraudlens-aks-demo"
environment         = "aks-demo"
vnet_address_space  = ["10.50.0.0/16"]
aks_subnet_prefixes = ["10.50.0.0/23"]
sku_tier            = "Free"
system_vm_size      = "Standard_B2s"
user_pool_enabled   = true
user_vm_size        = "Standard_D2as_v5"
user_min_count      = 1
user_max_count      = 2
acr_enabled         = false
monitoring_enabled  = false
budget_amount_usd   = 15
