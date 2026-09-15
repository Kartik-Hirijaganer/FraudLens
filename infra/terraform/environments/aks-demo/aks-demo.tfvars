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
# Standard DASv5 family quota is 0 in westus3, so a D2as_v5 pool cannot be created at any size.
# D2as_v4 has quota 10, ephemeral OS disks (no managed-disk charge), and is not burstable, so
# sustained CPU load produces a defensible HPA measurement (D2).
user_vm_size       = "Standard_D2as_v4"
user_min_count     = 1
user_max_count     = 2
acr_enabled        = false
monitoring_enabled = false
budget_amount_usd  = 15
