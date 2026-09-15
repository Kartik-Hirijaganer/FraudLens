# Ephemeral batch VM module (ADR-028) — an SSH-restricted Azure VM with system-assigned
# identity, two independent shutdown backstops, and optional GPU bootstrap. The module owns its
# isolated network so experiment roots cannot accidentally join an application trust boundary.

variable "name_prefix" {
  type        = string
  description = "Prefix used for every VM and network resource."
}

variable "resource_group_name" {
  type        = string
  description = "Resource group that contains the ephemeral host."
}

variable "location" {
  type        = string
  description = "Azure region for the experiment."
}

variable "vnet_address_space" {
  type        = list(string)
  description = "Address space for the isolated experiment VNet."
  default     = ["10.40.0.0/16"]
}

variable "subnet_address_prefixes" {
  type        = list(string)
  description = "Address prefixes for the single experiment subnet."
  default     = ["10.40.1.0/24"]
}

variable "operator_cidr" {
  type        = string
  description = "Single operator CIDR allowed to reach SSH; supplied at plan time."
  sensitive   = true

  validation {
    condition     = can(cidrhost(var.operator_cidr, 0))
    error_message = "operator_cidr must be a valid IPv4 or IPv6 CIDR."
  }
}

variable "admin_username" {
  type        = string
  description = "Non-root SSH account provisioned on the VM."
  default     = "fraudlens"
}

variable "ssh_public_key" {
  type        = string
  description = "Operator SSH public key; supplied at plan time and never committed."
  sensitive   = true
}

variable "vm_size" {
  type        = string
  description = "Allowed CPU or GPU experiment VM SKU."

  validation {
    condition = contains([
      "Standard_E16ads_v5",
      "Standard_E32ads_v5",
      "Standard_NC24ads_A100_v4",
      "Standard_NV36ads_A10_v5",
      "Standard_NC4as_T4_v3",
    ], var.vm_size)
    error_message = "vm_size must be an approved FraudLens CPU or GPU experiment SKU."
  }
}

variable "gpu_enabled" {
  type        = bool
  description = "Install the Azure NVIDIA extension and GPU container runtime when true."
  default     = false
}

variable "gpu_driver_install" {
  type        = string
  description = "GPU driver installation strategy; only extension is supported on Azure."
  default     = "extension"

  validation {
    condition     = contains(["extension", "none"], var.gpu_driver_install)
    error_message = "gpu_driver_install must be extension or none."
  }
}

variable "vllm_image" {
  type        = string
  description = "Version-pinned vLLM image pre-pulled only on GPU hosts."
  default     = "vllm/vllm-openai:v0.10.2"
}

variable "spot_enabled" {
  type        = bool
  description = "Use Spot priority when quota and capacity permit it."
  default     = false
}

variable "eviction_policy" {
  type        = string
  description = "Spot eviction behavior; Deallocate preserves the OS disk for resume."
  default     = "Deallocate"

  validation {
    condition     = contains(["Deallocate", "Delete"], var.eviction_policy)
    error_message = "eviction_policy must be Deallocate or Delete."
  }
}

variable "max_bid_price" {
  type        = number
  description = "Maximum Spot price in USD/hour; -1 accepts the current Spot rate."
  default     = -1
}

variable "os_disk_size_gb" {
  type        = number
  description = "Persistent OS/checkpoint disk size."
  default     = 128

  validation {
    condition     = var.os_disk_size_gb >= 128
    error_message = "os_disk_size_gb must be at least 128 GiB for checkpoints."
  }
}

variable "os_disk_storage_account_type" {
  type        = string
  description = "Managed OS disk storage class."
  default     = "Premium_LRS"
}

variable "auto_shutdown_time" {
  type        = string
  description = "Daily UTC platform shutdown time in HHmm form, normally now plus eight hours."

  validation {
    condition     = can(regex("^(?:[01][0-9]|2[0-3])[0-5][0-9]$", var.auto_shutdown_time))
    error_message = "auto_shutdown_time must be a UTC HHmm value."
  }
}

variable "watchdog_hours" {
  type        = number
  description = "Hours after boot before the in-VM watchdog deallocates the host."
  default     = 8

  validation {
    condition     = var.watchdog_hours > 0 && var.watchdog_hours <= 24
    error_message = "watchdog_hours must be greater than zero and no more than 24."
  }
}

variable "grant_self_deallocate" {
  type        = bool
  description = "Grant the managed identity VM Contributor on this VM for watchdog deallocation."
  default     = true
}

variable "tags" {
  type        = map(string)
  description = "Governance tags applied to every taggable resource."
  default     = {}
}

locals {
  cpu_sizes = toset([
    "Standard_E16ads_v5",
    "Standard_E32ads_v5",
  ])
  gpu_sizes = toset([
    "Standard_NC24ads_A100_v4",
    "Standard_NV36ads_A10_v5",
    "Standard_NC4as_T4_v3",
  ])
}

resource "azurerm_virtual_network" "this" {
  name                = "${var.name_prefix}-vnet"
  location            = var.location
  resource_group_name = var.resource_group_name
  address_space       = var.vnet_address_space
  tags                = var.tags
}

resource "azurerm_subnet" "this" {
  name                 = "${var.name_prefix}-subnet"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = var.subnet_address_prefixes
  service_endpoints    = ["Microsoft.Storage"]
}

resource "azurerm_public_ip" "this" {
  name                = "${var.name_prefix}-pip"
  location            = var.location
  resource_group_name = var.resource_group_name
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = var.tags
}

resource "azurerm_network_security_group" "this" {
  name                = "${var.name_prefix}-nsg"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags

  security_rule {
    name                       = "operator-ssh"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefix      = var.operator_cidr
    destination_address_prefix = "*"
  }
}

resource "azurerm_subnet_network_security_group_association" "this" {
  subnet_id                 = azurerm_subnet.this.id
  network_security_group_id = azurerm_network_security_group.this.id
}

resource "azurerm_network_interface" "this" {
  #checkov:skip=CKV_AZURE_119:An ephemeral public IP is required for operator SSH and is restricted to one sensitive CIDR by the subnet NSG.
  name                = "${var.name_prefix}-nic"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags

  ip_configuration {
    name                          = "primary"
    subnet_id                     = azurerm_subnet.this.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.this.id
  }
}

resource "azurerm_linux_virtual_machine" "this" {
  #checkov:skip=CKV_AZURE_50:The shared module permits Azure's managed NVIDIA extension only for allowlisted GPU hosts; the CPU root creates no extension.
  name                            = "${var.name_prefix}-vm"
  computer_name                   = "fraudlens-batch"
  location                        = var.location
  resource_group_name             = var.resource_group_name
  size                            = var.vm_size
  admin_username                  = var.admin_username
  disable_password_authentication = true
  network_interface_ids           = [azurerm_network_interface.this.id]
  priority                        = var.spot_enabled ? "Spot" : "Regular"
  eviction_policy                 = var.spot_enabled ? var.eviction_policy : null
  max_bid_price                   = var.spot_enabled ? var.max_bid_price : null
  secure_boot_enabled             = true
  vtpm_enabled                    = true
  provision_vm_agent              = true
  allow_extension_operations      = var.gpu_enabled
  custom_data = base64encode(templatefile("${path.module}/cloud-init.yaml.tftpl", {
    admin_username = var.admin_username
    gpu_enabled    = var.gpu_enabled
    vllm_image     = var.vllm_image
    watchdog_hours = var.watchdog_hours
  }))
  tags = var.tags

  admin_ssh_key {
    username   = var.admin_username
    public_key = var.ssh_public_key
  }

  identity {
    type = "SystemAssigned"
  }

  os_disk {
    name                 = "${var.name_prefix}-osdisk"
    caching              = "ReadWrite"
    storage_account_type = var.os_disk_storage_account_type
    disk_size_gb         = var.os_disk_size_gb
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "0001-com-ubuntu-server-jammy"
    sku       = "22_04-lts-gen2"
    version   = "latest"
  }

  boot_diagnostics {}

  lifecycle {
    precondition {
      condition = (
        var.gpu_enabled && contains(local.gpu_sizes, var.vm_size)
        ) || (
        !var.gpu_enabled && contains(local.cpu_sizes, var.vm_size)
      )
      error_message = "gpu_enabled and vm_size must select the same approved host class."
    }
  }
}

resource "azurerm_virtual_machine_extension" "gpu_driver" {
  count                      = var.gpu_enabled && var.gpu_driver_install == "extension" ? 1 : 0
  name                       = "NvidiaGpuDriverLinux"
  virtual_machine_id         = azurerm_linux_virtual_machine.this.id
  publisher                  = "Microsoft.HpcCompute"
  type                       = "NvidiaGpuDriverLinux"
  type_handler_version       = "1.10"
  auto_upgrade_minor_version = true
  automatic_upgrade_enabled  = true
  tags                       = var.tags
}

resource "azurerm_dev_test_global_vm_shutdown_schedule" "this" {
  virtual_machine_id    = azurerm_linux_virtual_machine.this.id
  location              = var.location
  enabled               = true
  daily_recurrence_time = var.auto_shutdown_time
  timezone              = "UTC"
  tags                  = var.tags

  notification_settings {
    enabled = false
  }
}

resource "azurerm_role_assignment" "self_deallocate" {
  count                = var.grant_self_deallocate ? 1 : 0
  scope                = azurerm_linux_virtual_machine.this.id
  role_definition_name = "Virtual Machine Contributor"
  principal_id         = azurerm_linux_virtual_machine.this.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

output "public_ip" {
  description = "Public IP used only for operator SSH."
  value       = azurerm_public_ip.this.ip_address
}

output "ssh_command" {
  description = "Operator command for connecting to the experiment host."
  value       = "ssh ${var.admin_username}@${azurerm_public_ip.this.ip_address}"
}

output "vm_name" {
  description = "Name of the ephemeral virtual machine."
  value       = azurerm_linux_virtual_machine.this.name
}

output "vm_size" {
  description = "Provisioned Azure VM SKU."
  value       = azurerm_linux_virtual_machine.this.size
}

output "spot" {
  description = "Whether the host uses Spot priority."
  value       = var.spot_enabled
}

output "identity_principal_id" {
  description = "System-assigned identity principal used for least-privilege data access."
  value       = azurerm_linux_virtual_machine.this.identity[0].principal_id
}

output "subnet_id" {
  description = "Experiment subnet resource id."
  value       = azurerm_subnet.this.id
}
