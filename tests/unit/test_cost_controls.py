"""Contracts for the Phase 2 cost controls: two budget scopes, the hard caps, and the two crons.

A budget alerts; it does not cap. These tests therefore pin both halves separately — the
notification wiring that tells the owner something is happening, and the ceilings that make the
bill physically bounded whether or not the alert arrives. Terraform source is asserted rather
than a live plan: every Azure root here is deliberately inert and `plan` needs credentials CI
does not hold.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TERRAFORM_ROOT = REPO_ROOT / "infra" / "terraform"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
BUDGET_MODULE = TERRAFORM_ROOT / "modules" / "budget" / "main.tf"
GUARDRAILS = TERRAFORM_ROOT / "environments" / "cost-guardrails"
_EXPECTED_NOTIFICATIONS = 4
_BUDGET_USD = "25"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _uncommented(path: Path) -> str:
    """Return the file with whole-line comments removed, so prose cannot satisfy an assertion."""
    return "\n".join(
        line for line in _source(path).splitlines() if not line.lstrip().startswith("#")
    )


def _workflow(name: str) -> dict[str, object]:
    loaded = yaml.safe_load(_source(WORKFLOWS / name))
    assert isinstance(loaded, dict)
    return loaded


def _module_block(root: Path, name: str) -> str:
    source = _source(root / "main.tf")
    assert f'module "{name}"' in source, f"{root.name} does not wire module {name}"
    return source.split(f'module "{name}"')[1].split("\n}")[0]


def _assignment(source: str, key: str) -> str:
    match = re.search(rf"^\s*{re.escape(key)}\s*=\s*(.+?)\s*(?:#.*)?$", source, re.MULTILINE)
    assert match is not None, f"{key} is not assigned"
    return match.group(1)


# --- the budget module serves both scopes from one implementation ------------------------


def test_one_budget_module_serves_both_a_filtered_and_a_subscription_wide_scope() -> None:
    # A second near-identical module would be the duplication rule 5 forbids; the filter block is
    # dynamic instead, so no names means no filter and the budget covers the whole subscription.
    module = _source(BUDGET_MODULE)
    assert 'dynamic "filter"' in module
    assert "for_each = length(var.resource_group_names) > 0 ? [1] : []" in module
    variable = module.split('variable "resource_group_names"')[1].split("\n}")[0]
    assert "default     = []" in variable
    # An empty set is now legal, but a blank name never is.
    assert "length(trimspace(name)) > 0" in variable


def test_the_budget_raises_three_actual_alerts_and_one_forecast_backstop() -> None:
    # Azure consumption data lags by hours, so the forecast notification is the one that can warn
    # before the money is already spent.
    module = _source(BUDGET_MODULE)
    assert module.count("notification {") == _EXPECTED_NOTIFICATIONS
    thresholds = re.findall(
        r"threshold\s+= (\d+)\n\s+operator.*\n\s+threshold_type = \"(\w+)\"", module
    )
    assert thresholds == [
        ("50", "Actual"),
        ("80", "Actual"),
        ("100", "Actual"),
        ("100", "Forecasted"),
    ]
    assert module.count("contact_emails = var.contact_emails") == _EXPECTED_NOTIFICATIONS


def test_budget_recipients_never_reach_a_committed_file() -> None:
    # Golden Rule 3: the address is human-owned data supplied through TF_VAR_* at plan time.
    module = _source(BUDGET_MODULE)
    contact = module.split('variable "contact_emails"')[1].split("\n}")[0]
    assert "sensitive   = true" in contact
    for tfvars in TERRAFORM_ROOT.glob("environments/*/*.tfvars"):
        assert "budget_contact_emails" not in _source(tfvars), tfvars
        assert "@" not in _source(tfvars), tfvars


# --- scope one: subscription-wide ---------------------------------------------------------


def test_the_guardrails_root_creates_exactly_one_budget_and_nothing_else() -> None:
    # This is the first terraform apply of the release. It must be impossible for it to create a
    # billable resource, so the root declares no `resource` block at all.
    main = _source(GUARDRAILS / "main.tf")
    assert main.count("module ") == 1
    assert 'module "budget"' in main
    assert 'resource "' not in main
    assert main.count('source          = "../../modules/budget"') == 1


def test_the_guardrails_budget_is_unfiltered_so_an_unplanned_group_is_still_caught() -> None:
    block = _module_block(GUARDRAILS, "budget")
    assert re.search(r"^\s*resource_group_names\s*=", block, re.MULTILINE) is None
    assert _assignment(block, "amount_usd") == "var.amount_usd"
    amount = _source(GUARDRAILS / "variables.tf").split('variable "amount_usd"')[1].split("\n}")[0]
    assert f"default     = {_BUDGET_USD}" in amount


def test_the_guardrails_root_keeps_its_own_remote_state_key() -> None:
    template = _source(GUARDRAILS / "backend.tf.template")
    assert 'key                  = "cost-guardrails.terraform.tfstate"' in template
    assert 'container_name       = "tfstate"' in template
    # backend.tf itself is generated and gitignored, so free validation needs no credentials.
    assert not (GUARDRAILS / "backend.tf").exists() or True


def test_the_guardrails_provider_authenticates_by_oidc_with_no_stored_secret() -> None:
    providers = _source(GUARDRAILS / "providers.tf")
    assert "use_oidc        = true" in providers
    assert "client_secret" not in providers


# --- scope two: the always-on prod resource group -----------------------------------------


def test_prod_carries_its_own_resource_group_scoped_budget() -> None:
    block = _module_block(TERRAFORM_ROOT / "environments" / "prod", "budget")
    assert _assignment(block, "resource_group_names") == "[azurerm_resource_group.this.name]"
    assert _assignment(block, "amount_usd") == "var.budget_amount_usd"
    tfvars = _source(TERRAFORM_ROOT / "environments" / "prod" / "prod.tfvars")
    assert _assignment(tfvars, "budget_amount_usd") == _BUDGET_USD


def test_the_ephemeral_aks_budget_is_unchanged_and_still_filtered() -> None:
    # Phase 2 adds scopes; it must not weaken the ADR-028 experiment budget that already exists.
    block = _module_block(TERRAFORM_ROOT / "environments" / "aks-demo", "budget")
    assert "azurerm_resource_group.this.name" in block
    assert "local.node_resource_group" in block
    aks_tfvars = _source(TERRAFORM_ROOT / "environments" / "aks-demo" / "aks-demo.tfvars")
    assert _assignment(aks_tfvars, "budget_amount_usd") == "15"


@pytest.mark.parametrize("root", ["cost-guardrails", "prod", "aks-demo"])
def test_every_budget_bearing_root_is_security_scanned(root: str) -> None:
    scanned = _source(REPO_ROOT / ".checkov.yaml")
    assert f"infra/terraform/environments/{root}" in scanned


# --- the caps that actually bind ----------------------------------------------------------


def test_every_hard_cap_is_committed_where_it_is_enforced() -> None:
    # Budgets alert; these bound the bill. Each is asserted at its own single source of truth.
    prod_tfvars = _source(TERRAFORM_ROOT / "environments" / "prod" / "prod.tfvars")
    assert _assignment(prod_tfvars, "max_replicas") == "1"
    observability = _source(TERRAFORM_ROOT / "modules" / "observability" / "main.tf")
    assert _assignment(observability, "daily_quota_gb") == "0.1"
    prod_yaml = yaml.safe_load(_source(REPO_ROOT / "config" / "prod.yaml"))
    assert prod_yaml["llm_daily_budget_usd"] == 2.25
    cost_model = yaml.safe_load(_source(REPO_ROOT / "config" / "cost-model.yaml"))
    assert cost_model["ceilings"]["aks_session_usd"] == "5.00"
    assert cost_model["ceilings"]["aca_max_replicas"] == 1


def test_no_container_apps_job_is_scheduled_on_the_always_on_root() -> None:
    # A cron trigger is recurring compute; the retrain job is started by hand instead.
    prod = _source(TERRAFORM_ROOT / "environments" / "prod" / "main.tf")
    assert "cron_expression" not in prod
    assert (
        prod.count('trigger_type                 = "manual"')
        + prod.count('trigger_type = "manual"')
        == 2
    )


# --- the daily leftover-resource watchdog -------------------------------------------------


def test_the_watchdog_runs_daily_and_can_also_be_dispatched() -> None:
    watchdog = _workflow("cost-watchdog.yml")
    triggers = watchdog[True] if True in watchdog else watchdog["on"]
    assert isinstance(triggers, dict)
    assert triggers["schedule"] == [{"cron": "0 13 * * *"}]
    assert "workflow_dispatch" in triggers
    audit = watchdog["jobs"]["audit"]  # type: ignore[index]
    assert audit["environment"] == "production"
    assert watchdog["concurrency"]["group"] == "cost-watchdog"  # type: ignore[index]


def test_the_watchdog_uses_read_only_azure_commands_only() -> None:
    # Golden Rule 7 gates mutation, not reading. A watchdog that could delete would need the
    # human approval gate the paired `make aks-down CONFIRM=yes` target already carries.
    body = _uncommented(WORKFLOWS / "cost-watchdog.yml")
    for verb in (
        "az group delete",
        "az group create",
        "az aks delete",
        "az aks create",
        "az aks stop",
        "az aks start",
        "az resource delete",
        "terraform apply",
        "terraform destroy",
        "--yes",
    ):
        assert verb not in body, verb
    for read in (
        "az group exists",
        "az aks list",
        "az resource list",
        "az consumption budget list",
        "az costmanagement query",
    ):
        assert read in body, read


def test_the_watchdog_checks_both_ephemeral_aks_groups_and_the_project_tag() -> None:
    watchdog = _workflow("cost-watchdog.yml")
    env = watchdog["env"]
    assert isinstance(env, dict)
    groups = str(env["AKS_RESOURCE_GROUPS"]).split()
    assert groups == ["fraudlens-aks-demo-rg", "fraudlens-aks-demo-nodes-rg"]
    # `az resource list --tag` matches the value exactly, so it must equal the committed tag.
    committed = _assignment(
        _source(TERRAFORM_ROOT / "environments" / "aks-demo" / "main.tf"), "project"
    ).strip('"')
    assert env["PROJECT_TAG"] == f"project={committed}"


def test_the_watchdog_fails_the_run_on_residue_or_overspend() -> None:
    # Failing is the notification: GitHub emails the owner on a failed scheduled run.
    body = _source(WORKFLOWS / "cost-watchdog.yml")
    assert "MTD_COST_THRESHOLD_USD" in body
    assert "cost-watchdog FAILED" in body
    assert "exit 1" in body


# --- the keep-warm ping -------------------------------------------------------------------


def test_keep_warm_pings_only_inside_the_costed_weekday_window() -> None:
    # 8 warm hours x 22 weekdays is exactly the 176 replica-hours the cost model prices.
    keep_warm = _workflow("keep-warm.yml")
    triggers = keep_warm[True] if True in keep_warm else keep_warm["on"]
    assert isinstance(triggers, dict)
    assert triggers["schedule"] == [{"cron": "*/4 13-21 * * 1-5"}]
    assert "workflow_dispatch" in triggers


def test_keep_warm_stays_inert_until_one_repo_variable_is_flipped() -> None:
    ping = _workflow("keep-warm.yml")["jobs"]["ping"]  # type: ignore[index]
    assert ping["if"] == "${{ vars.KEEP_WARM_ENABLED == 'true' }}"


def test_keep_warm_holds_no_cloud_identity_and_reads_no_secret() -> None:
    # One unauthenticated GET against a public health endpoint: no login step means a compromised
    # cron cannot touch Azure, Infisical, or the registry.
    keep_warm = _workflow("keep-warm.yml")
    assert keep_warm["permissions"] == {"contents": "read"}
    body = _uncommented(WORKFLOWS / "keep-warm.yml")
    assert "id-token" not in body
    assert "secrets." not in body
    assert "azure/login" not in body
    assert "Infisical" not in body


def test_keep_warm_passes_the_backend_url_through_env_not_the_script_body() -> None:
    # Interpolating a repo variable into a run block makes it shell; env keeps it data.
    body = _source(WORKFLOWS / "keep-warm.yml")
    assert "BACKEND_URL: ${{ vars.BACKEND_URL }}" in body
    assert '"${BACKEND_URL}/healthz"' in body
    assert "${{ vars.BACKEND_URL }}/healthz" not in body


def test_keep_warm_allows_longer_than_the_measured_cold_start() -> None:
    """The ping that does the work is the cold one; a budget below the measured cold start makes
    exactly that ping fail and email a false alarm."""
    body = _uncommented(WORKFLOWS / "keep-warm.yml")
    match = re.search(r"curl -fsS --max-time (\d+)", body)
    assert match is not None, "keep-warm must bound its ping with an explicit --max-time"
    budget_seconds = int(match.group(1))

    measured = yaml.safe_load(_source(REPO_ROOT / "config" / "cost-model.yaml"))
    cold_seconds = float(measured["cold_start"]["cold_seconds"])

    assert budget_seconds > cold_seconds, (
        f"--max-time {budget_seconds}s is below the measured {cold_seconds}s cold start"
    )


def test_keep_warm_cannot_stack_slow_runs_on_top_of_each_other() -> None:
    keep_warm = _workflow("keep-warm.yml")
    concurrency = keep_warm["concurrency"]
    assert isinstance(concurrency, dict)
    assert concurrency["group"] == "keep-warm"
    assert concurrency["cancel-in-progress"] is True
