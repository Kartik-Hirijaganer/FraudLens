"""Summary: Structural contracts for the bounded Azure data-batch Terraform session.

Key classes:
- None.

Key functions:
- test_data_batch_root_is_west_us_3_payg: lock the approved region, SKU, and purchase path.
- test_batch_vm_has_network_identity_and_shutdown_controls: enforce the host trust boundary.
- test_experiment_storage_is_private_oauth_only: enforce storage and RBAC posture.
- test_make_targets_gate_every_mutation: keep plans free and mutations explicitly confirmed.

Notes:
- Terraform validate and Checkov provide semantic validation; these tests protect intent against
  configuration drift without requiring cloud credentials.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TERRAFORM = REPO_ROOT / "infra" / "terraform"
DATA_BATCH = TERRAFORM / "environments" / "data-batch"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_data_batch_root_is_west_us_3_payg() -> None:
    """The current quota decision must remain West US 3 E16ads v5 PAYG."""
    tfvars = _read(DATA_BATCH / "data-batch.tfvars")
    assert re.search(r'(?m)^location\s*=\s*"westus3"$', tfvars)
    assert re.search(r'(?m)^vm_size\s*=\s*"Standard_E16ads_v5"$', tfvars)
    assert re.search(r"(?m)^spot_enabled\s*=\s*false", tfvars)
    assert re.search(r"(?m)^watchdog_hours\s*=\s*8$", tfvars)
    assert re.search(r"(?m)^budget_amount_usd\s*=\s*15$", tfvars)


def test_data_batch_root_has_one_ephemeral_resource_group_and_modules() -> None:
    """The VM, storage, and budget compose into one teardown-scoped resource group."""
    main = _read(DATA_BATCH / "main.tf")
    assert main.count('resource "azurerm_resource_group"') == 1
    assert 'module "batch_vm"' in main
    assert 'module "experiment_storage"' in main
    assert 'module "budget"' in main
    assert "id   = module.batch_vm.identity_principal_id" in main
    assert 'type = "ServicePrincipal"' in main
    assert "id   = var.operator_principal_id" in main
    assert "type = var.operator_principal_type" in main
    assert 'lifecycle   = "ephemeral"' in main
    assert "run_id      = var.run_id" in main


def test_batch_vm_has_network_identity_and_shutdown_controls() -> None:
    """Only SSH is exposed and both platform and in-guest shutdown controls are present."""
    module = _read(TERRAFORM / "modules" / "batch_vm" / "main.tf")
    cloud_init = _read(TERRAFORM / "modules" / "batch_vm" / "cloud-init.yaml.tftpl")
    assert 'destination_port_range     = "22"' in module
    assert "source_address_prefix      = var.operator_cidr" in module
    assert "disable_password_authentication = true" in module
    assert 'type = "SystemAssigned"' in module
    assert 'role_definition_name = "Virtual Machine Contributor"' in module
    assert 'resource "azurerm_dev_test_global_vm_shutdown_schedule"' in module
    assert "admin_username = var.admin_username" in module
    assert "-g ${admin_username} /mnt/fraudlens" in cloud_init
    assert "OnBootSec=${watchdog_hours}h" in cloud_init
    assert "az vm deallocate --ids" in cloud_init
    assert "metadata/instance/compute/resourceId" in cloud_init


def test_experiment_storage_is_private_oauth_only() -> None:
    """Synthetic inputs still use private containers, firewalling, OAuth, and scoped RBAC."""
    module = _read(TERRAFORM / "modules" / "experiment_storage" / "main.tf")
    provider = _read(DATA_BATCH / "providers.tf")
    assert "storage_use_azuread = true" in provider
    assert "allow_nested_items_to_be_public   = false" in module
    assert "shared_access_key_enabled         = false" in module
    assert "default_to_oauth_authentication   = true" in module
    assert 'default_action             = "Deny"' in module
    assert 'bypass                     = ["None"]' in module
    assert 'container_access_type = "private"' in module
    assert 'role_definition_name             = "Storage Blob Data Contributor"' in module
    assert "principal_type                   = var.principals[count.index].type" in module
    assert "delete_after_days_since_modification_greater_than = var.retention_days" in module


def test_budget_has_escalating_actual_and_forecast_alerts() -> None:
    """The cloud alert supplements, but never replaces, local admission controls."""
    module = _read(TERRAFORM / "modules" / "budget" / "main.tf")
    for threshold in (50, 80, 100):
        assert f"threshold      = {threshold}" in module
    assert module.count('threshold_type = "Actual"') == 3
    assert module.count('threshold_type = "Forecasted"') == 1
    assert 'name     = "ResourceGroupName"' in module


def test_make_targets_gate_every_mutation() -> None:
    """Plan and verification are read-only; create/upload/start/destroy require confirmation."""
    makefile = _read(REPO_ROOT / "Makefile")
    for target in ("data-batch-up", "data-batch-upload", "data-batch-start", "data-batch-down"):
        recipe = makefile.split(f"{target}:", maxsplit=1)[1].split("\n\n", maxsplit=1)[0]
        assert '"$(CONFIRM)" = "yes"' in recipe
    plan_recipe = makefile.split("data-batch-plan:", maxsplit=1)[1].split("\n\n", maxsplit=1)[0]
    assert "terraform" in plan_recipe
    assert " apply " not in plan_recipe
    assert " destroy " not in plan_recipe
    assert "init -backend=false" in plan_recipe
    assert "TF_ROOTS ?=" in makefile


def test_remote_state_and_local_plan_artifacts_are_not_committed() -> None:
    """Only the backend template and lockfile are versioned."""
    backend = _read(DATA_BATCH / "backend.tf.template")
    gitignore = _read(REPO_ROOT / ".gitignore")
    assert 'key                  = "data-batch.terraform.tfstate"' in backend
    assert "use_azuread_auth     = true" in backend
    assert "*.tfplan" in gitignore
    assert "infra/terraform/environments/*/backend.tf" in gitignore
