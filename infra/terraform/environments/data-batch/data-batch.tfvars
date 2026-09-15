# West US 3 data-batch configuration (non-secret). Account ids, operator CIDR/key, budget contact,
# budget month, and the rolling +8-hour shutdown time are supplied by the guarded Make targets.

location                = "westus3"
name_prefix             = "fraudlens-data-batch"
environment             = "data-batch"
vm_size                 = "Standard_E16ads_v5"
spot_enabled            = false # Spot quota is 3 vCPUs; this 16-vCPU host must use PAYG.
max_bid_price           = -1
os_disk_size_gb         = 128
watchdog_hours          = 8
storage_account_name    = "fraudlensbatchkh2609"
storage_retention_days  = 30
budget_amount_usd       = 15
vnet_address_space      = ["10.40.0.0/16"]
subnet_address_prefixes = ["10.40.1.0/24"]
