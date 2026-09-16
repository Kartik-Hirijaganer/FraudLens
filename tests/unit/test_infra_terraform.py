"""Cost and correctness contracts for the committed Terraform sources.

These assert the source of a `terraform plan`, not a live apply: every Azure root in this repo is
deliberately inert, and running `plan` needs subscription credentials that CI does not hold. Each
test therefore pins the exact construct whose absence produced a verified defect.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TERRAFORM_ROOT = REPO_ROOT / "infra" / "terraform"


def _source(relative: str) -> str:
    return (TERRAFORM_ROOT / relative).read_text(encoding="utf-8")


def _assignment(source: str, key: str) -> str:
    match = re.search(rf"^\s*{re.escape(key)}\s*=\s*(.+?)\s*(?:#.*)?$", source, re.MULTILINE)
    assert match is not None, f"{key} is not assigned"
    return match.group(1)


def test_prod_requests_no_network_so_no_load_balancer_is_provisioned() -> None:
    # A custom-network Container Apps environment provisions a Standard Load Balancer and public
    # IP as fixed infrastructure (~$22/month) that an egress-only gateway never uses.
    assert _assignment(_source("environments/prod/prod.tfvars"), "apps_subnet_prefixes") == "[]"
    assert _assignment(_source("environments/prod/main.tf"), "infrastructure_subnet_id") == "null"


def test_networking_creates_no_vnet_when_no_subnet_is_requested() -> None:
    networking = _source("modules/networking/main.tf")
    assert 'resource "azurerm_virtual_network" "this" {\n  count' in networking
    assert (
        "subnet_prefix_count = length(var.apps_subnet_prefixes) + length(var.aks_subnet_prefixes)"
        in networking
    )
    # The AKS root still asks for a node subnet, so its VNet must survive the same gate.
    aks_demo = _source("environments/aks-demo/aks-demo.tfvars")
    assert _assignment(aks_demo, "aks_subnet_prefixes") != "[]"


def test_gateway_accepts_a_platform_managed_network() -> None:
    gateway = _source("modules/gateway_app/main.tf")
    variable = gateway.split('variable "infrastructure_subnet_id"')[1].split("}")[0]
    assert "nullable    = true" in variable
    assert "default     = null" in variable


def test_gateway_publishes_the_stable_ingress_fqdn() -> None:
    # latest_revision_fqdn changes with every revision, so a promoted blue/green deploy would
    # move the public URL the frontend proxy and the recruiter-facing link both point at.
    gateway = _source("modules/gateway_app/main.tf")
    assert "azurerm_container_app.this.ingress[0].fqdn" in gateway
    assert "latest_revision_fqdn" not in gateway


def test_cors_origins_are_json_encoded_for_pydantic_settings() -> None:
    # FRAUDLENS_CORS_ALLOW_ORIGINS is list[str]; pydantic-settings decodes it as JSON, so a
    # comma-joined value (and an empty list joined to "") aborts the container at startup.
    gateway = _source("modules/gateway_app/main.tf")
    assert "value = jsonencode(var.cors_allow_origins)" in gateway
    assert 'join(",", var.cors_allow_origins)' not in gateway


def test_the_gateway_allows_exactly_the_published_frontend_origin() -> None:
    # The browser reaches the API same-origin through the Vercel proxy, so CORS is a backstop
    # rather than the mechanism. An empty list would still deny a direct cross-origin call; a
    # wildcard would hand the credentialed API to any site that asked.
    origins = _assignment(_source("environments/prod/prod.tfvars"), "gateway_cors_origins")
    assert origins == '["https://fraud-lens-amber.vercel.app"]'
    assert "*" not in origins


def test_terraform_does_not_revert_the_deploy_workflow_promotion() -> None:
    gateway = _source("modules/gateway_app/main.tf")
    ignored = gateway.split("ignore_changes = [")[1].split("\n  ]")[0]
    assert "template[0].container[0].image" in ignored
    assert "ingress[0].traffic_weight" in ignored
    assert "secret," in ignored


def test_readiness_probe_matches_the_cached_remote_probes() -> None:
    probe = _source("modules/gateway_app/main.tf").split("readiness_probe {")[1].split("}")[0]
    assert _assignment(probe, "interval_seconds") == "30"
    assert _assignment(probe, "failure_count_threshold") == "3"


def test_log_ingestion_is_capped() -> None:
    # PerGB2018 bills ~$2.30/GB with no ceiling; the workspace must stop ingesting instead.
    assert _assignment(_source("modules/observability/main.tf"), "daily_quota_gb") == "0.1"


def test_user_node_pool_defaults_to_regular_priority() -> None:
    aks = _source("modules/aks/main.tf")
    assert _assignment(aks, "priority") == 'var.user_pool_spot_enabled ? "Spot" : "Regular"'
    assert _assignment(aks, "eviction_policy") == 'var.user_pool_spot_enabled ? "Delete" : null'
    assert _assignment(aks, "spot_max_price") == "var.user_pool_spot_enabled ? -1 : null"
    spot_variable = aks.split('variable "user_pool_spot_enabled"')[1].split("}")[0]
    assert "default     = false" in spot_variable
    taints = _assignment(aks, "node_taints")
    assert taints.startswith("var.user_pool_spot_enabled ?") and taints.endswith(": []")


@pytest.mark.parametrize(
    "relative",
    ["modules/aks/main.tf", "environments/aks-demo/aks-demo.tfvars"],
)
def test_no_root_or_module_selects_a_zero_quota_sku(relative: str) -> None:
    # Standard DASv5 family quota is 0 in westus3: a D2as_v5 pool fails with QuotaExceeded at any
    # size, and a Spot increase cannot lift a zero family limit.
    assert "Standard_D2as_v5" not in _source(relative)
    assert "Standard_D2as_v4" in _source(relative)


def test_the_user_pool_keeps_its_release_node_cap() -> None:
    aks = _source("modules/aks/main.tf")
    assert "var.user_max_count >= var.user_min_count && var.user_max_count <= 2" in aks


def test_prod_bounds_replicas_and_starts_no_scheduled_job() -> None:
    assert _assignment(_source("environments/prod/prod.tfvars"), "max_replicas") == "1"
    prod = _source("environments/prod/main.tf")
    retrain = prod.split('module "job_retrain"')[1].split('module "job_batch_score"')[0]
    assert _assignment(retrain, "trigger_type") == '"manual"'
    assert "cron_expression" not in retrain
    assert "retrain_cron" not in _source("environments/prod/variables.tf")


def test_the_prod_root_can_delete_the_group_azure_adds_resources_to() -> None:
    """Azure attaches resources to this group that no Terraform resource owns.

    An Application Insights component always auto-creates an "Application Insights Smart
    Detection" action group beside itself. With the provider default, its presence refuses the
    resource group deletion outright, so a location change or a teardown destroys everything
    Terraform manages and then strands the rest -- which is exactly how the first eastus apply
    ended. This root owns its group outright, so deleting the group deletes only what this root
    created plus what Azure attached to it.
    """
    providers = _source("environments/prod/providers.tf")
    assert "prevent_deletion_if_contains_resources = false" in providers
    block = providers.split("resource_group {")[1].split("}")[0]
    assert "prevent_deletion_if_contains_resources" in block


def test_every_terraform_root_is_security_scanned() -> None:
    scanned = (REPO_ROOT / ".checkov.yaml").read_text(encoding="utf-8")
    roots = {path.parent.name for path in TERRAFORM_ROOT.glob("environments/*/main.tf")}
    for root in roots:
        assert f"infra/terraform/environments/{root}" in scanned, root
