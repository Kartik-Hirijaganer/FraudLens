"""Deploy-flow contract tests (plan §15.7 / §17 / Phase 14). These assert the fast & reliable
deploy invariants as STRUCTURE in the committed workflow + Terraform files (no cloud, no apply):
build-once-promote-many, the startup-probe budget exceeding the cold-start target, revision @0%
-> gated migration -> smoke -> promote-or-abort, and app rollout decoupled from terraform.
Run via `pytest -k deploy`."""

from __future__ import annotations

import math
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
TERRAFORM = REPO_ROOT / "infra" / "terraform"
LOWERCASE_OWNER_SCRIPT = (
    "owner=\"$(printf '%s' \"$GITHUB_REPOSITORY_OWNER\" | tr '[:upper:]' '[:lower:]')\""
)


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _deploy_backend() -> dict:
    return _load_yaml(WORKFLOWS / "deploy-backend.yml")


def _deploy_backend_flat() -> str:
    """Whitespace-normalized workflow text — robust to YAML line-wrapping for content checks."""
    return re.sub(r"\s+", " ", (WORKFLOWS / "deploy-backend.yml").read_text())


def _build_base_flat() -> str:
    """Whitespace-normalized base-image workflow text for GHCR reference checks."""
    return re.sub(r"\s+", " ", (WORKFLOWS / "build-base.yml").read_text())


def _deploy_aks() -> dict:
    return _load_yaml(WORKFLOWS / "deploy-aks.yml")


def _deploy_aks_flat() -> str:
    return re.sub(r"\s+", " ", (WORKFLOWS / "deploy-aks.yml").read_text())


def _job_script(job: dict) -> str:
    """Concatenate + normalize a job's step `run` scripts (for scoped content assertions)."""
    runs = " ".join(step.get("run", "") for step in job.get("steps", []))
    return re.sub(r"\s+", " ", runs)


# --- Build-once-promote-many -------------------------------------------------------------------


def test_image_is_built_exactly_once_and_reused() -> None:
    """ONE build/push step; stage + infra reuse that same image ref (never rebuilt downstream)."""
    text = (WORKFLOWS / "deploy-backend.yml").read_text()
    assert text.count("docker/build-push-action") == 1
    # No second image build in the rollout path (no rebuild per environment / per step).
    assert "az acr build" not in text
    assert "docker build " not in text
    jobs = _deploy_backend()["jobs"]
    assert jobs["build-push"]["outputs"]["image"]
    # The staged revision and the infra plan both consume the build-once output ref.
    assert "needs.build-push.outputs.image" in _job_script(jobs["stage"])
    assert "needs.build-push.outputs.image" in _job_script(jobs["infra"])


def test_image_tagged_by_commit_sha() -> None:
    """The single image is immutable, tagged by the deployed commit SHA (build-once identity).

    The tag reads `DEPLOY_SHA` rather than the event field directly, because the deploy now also
    runs from a `workflow_dispatch` against a release branch, where `github.event.workflow_run` is
    absent. Both paths must still resolve to the SHA actually being deployed -- a tag that fell
    back to empty would push `fraudlens-backend:` and break build-once identity outright.
    """
    flat = _deploy_backend_flat()
    assert LOWERCASE_OWNER_SCRIPT in flat
    assert 'image_name="ghcr.io/${owner}/fraudlens-backend"' in flat
    assert "image=${image_name}:${DEPLOY_SHA}" in flat
    assert "DEPLOY_SHA: ${{ github.event.workflow_run.head_sha || github.sha }}" in flat


def test_ghcr_image_references_are_lowercase() -> None:
    """GHCR/Docker repository names must be lowercase even when the GitHub owner has caps."""
    flat = _build_base_flat()
    assert LOWERCASE_OWNER_SCRIPT in flat
    assert "base_image=ghcr.io/${owner}/fraudlens-base" in flat
    assert "${{ steps.image.outputs.base_image }}:latest" in flat
    assert "github.event_name == 'pull_request' && 'type=gha,mode=max'" in flat


# --- Revision @0% -> gated migration -> smoke -> promote-or-abort ------------------------------


def test_revision_is_staged_before_promotion() -> None:
    """A new revision is created with a suffix (Multiple mode => 0% traffic), not auto-promoted."""
    text = (WORKFLOWS / "deploy-backend.yml").read_text()
    assert "az containerapp update" in text
    assert "--revision-suffix" in text


def test_migration_is_gated_between_stage_and_promote() -> None:
    """Alembic upgrade runs AFTER staging and BEFORE smoke/promote — it gates promotion only."""
    jobs = _deploy_backend()["jobs"]
    assert "stage" in jobs["migrate"]["needs"]
    assert "migrate" in jobs["smoke"]["needs"]
    # Containment, not equality: `promote` also needs `build-push` for the revision suffix it
    # computes. What must hold is that smoke still gates promotion.
    assert "smoke" in jobs["promote"]["needs"]
    assert "alembic upgrade head" in _job_script(jobs["migrate"])


def test_promote_requires_green_smoke() -> None:
    """Promotion to 100% traffic happens only after smoke passes."""
    jobs = _deploy_backend()["jobs"]
    assert "smoke" in jobs["promote"]["needs"]
    promote_script = _job_script(jobs["promote"])
    assert "ingress traffic set" in promote_script
    assert "=100" in promote_script


def test_failed_smoke_or_migration_auto_aborts_keeping_previous_revision() -> None:
    """On smoke/migration failure the staged revision deactivates; the prior revision stays live."""
    jobs = _deploy_backend()["jobs"]
    abort = jobs["abort"]
    condition = abort["if"]
    assert "always()" in condition
    assert "needs.smoke.result == 'failure'" in condition
    assert "needs.migrate.result == 'failure'" in condition
    assert "revision deactivate" in _job_script(abort)


# --- Fast app rollout: terraform only when infra changes ---------------------------------------


def test_terraform_apply_is_gated_on_a_nonempty_plan() -> None:
    """The hot path skips terraform; apply runs only when `plan -detailed-exitcode` sees changes."""
    infra_script = _job_script(_deploy_backend()["jobs"]["infra"])
    assert "-detailed-exitcode" in infra_script
    assert "terraform apply" in infra_script
    # The rollout itself is an `az containerapp update`, not terraform apply (seconds, not minutes).
    stage_script = _job_script(_deploy_backend()["jobs"]["stage"])
    assert "terraform" not in stage_script
    assert "az containerapp update" in stage_script


# --- Resilience: retries / timeouts / no overlapping deploys -----------------------------------


def test_deploy_jobs_have_timeouts_and_no_overlap() -> None:
    """Each cloud job has a timeout; concurrency never cancels an in-flight deploy."""
    wf = _deploy_backend()
    assert wf["concurrency"]["cancel-in-progress"] is False
    for name in ("build-push", "infra", "stage", "migrate", "smoke", "promote", "abort"):
        assert wf["jobs"][name]["timeout-minutes"] >= 1


# --- Startup-probe budget exceeds the cold-start target (the #1 "deploy breaks" fix) -----------


def _int_default(hcl: str, var_name: str) -> int:
    block = re.search(rf'variable "{var_name}"\s*{{(.*?)}}', hcl, re.DOTALL)
    assert block, f"variable {var_name} not found"
    default = re.search(r"default\s*=\s*(\d+)", block.group(1))
    assert default, f"default for {var_name} not found"
    return int(default.group(1))


def test_startup_probe_budget_exceeds_cold_start() -> None:
    """failure_count_threshold * period must clear the ≤75s cold start so the platform never kills
    a still-loading ML container (plan §15.7)."""
    hcl = (TERRAFORM / "modules" / "gateway_app" / "main.tf").read_text()
    cold_start = _int_default(hcl, "cold_start_budget_seconds")
    period = _int_default(hcl, "startup_probe_period_seconds")
    # Mirror the module's local: ceil(budget / period) + 2 attempts of headroom.
    assert "ceil(var.cold_start_budget_seconds / var.startup_probe_period_seconds) + 2" in hcl
    threshold = math.ceil(cold_start / period) + 2
    assert threshold * period > cold_start
    assert cold_start <= 75  # the documented cold-start ceiling
    assert "startup_probe {" in hcl


# --- Trust-boundary invariants in the plan (also enforced by tf-validate) ----------------------


def test_gateway_is_external_https_only() -> None:
    """The gateway is the only external ingress and rejects insecure (HTTP) connections."""
    hcl = (TERRAFORM / "modules" / "gateway_app" / "main.tf").read_text()
    assert "external_enabled           = true" in hcl
    assert "allow_insecure_connections = false" in hcl
    assert 'revision_mode = "Multiple"' in hcl


def test_service_split_is_inert_in_v1() -> None:
    """The internal service split is validated but NOT applied (services_split_enabled = false)."""
    for env in ("dev", "prod"):
        tfvars = (TERRAFORM / "environments" / env / f"{env}.tfvars").read_text()
        assert re.search(r"services_split_enabled\s*=\s*false", tfvars)
    service_hcl = (TERRAFORM / "modules" / "service_app" / "main.tf").read_text()
    assert "external_enabled           = false" in service_hcl  # internal ingress only


def test_blob_lifecycle_policy_present() -> None:
    """A blob lifecycle policy (cool-tier + SAR-PDF expiry) bounds storage cost (plan §15.3)."""
    hcl = (TERRAFORM / "modules" / "blob" / "main.tf").read_text()
    assert "azurerm_storage_management_policy" in hcl
    assert "tier_to_cool_after_days_since_modification_greater_than" in hcl


def test_azure_runtime_backends_receive_required_env() -> None:
    """Gateway + jobs receive non-secret Azure runtime config for Blob and Job REST backends."""
    gateway_hcl = (TERRAFORM / "modules" / "gateway_app" / "main.tf").read_text()
    jobs_hcl = (TERRAFORM / "modules" / "jobs" / "main.tf").read_text()
    shared_env = {
        "FRAUDLENS_AZURE_MANAGED_IDENTITY_CLIENT_ID",
        "FRAUDLENS_AZURE_SUBSCRIPTION_ID",
        "FRAUDLENS_AZURE_RESOURCE_GROUP_NAME",
        "FRAUDLENS_AZURE_STORAGE_ACCOUNT_NAME",
        "FRAUDLENS_AZURE_STORAGE_CONTAINER_NAME",
        "FRAUDLENS_AZURE_STORAGE_SAR_PDF_CONTAINER_NAME",
    }
    for name in shared_env:
        assert name in gateway_hcl
        assert name in jobs_hcl
    assert "FRAUDLENS_AZURE_CONTAINER_APPS_RETRAIN_JOB_NAME" in gateway_hcl
    assert "FRAUDLENS_AZURE_CONTAINER_APPS_BATCH_SCORE_JOB_NAME" in gateway_hcl
    for env in ("dev", "prod"):
        hcl = (TERRAFORM / "environments" / env / "main.tf").read_text()
        assert "identity_client_id" in hcl
        assert "storage_account_name" in hcl
        assert "storage_container_name" in hcl
        assert "sar_pdf_container_name" in hcl
        assert "retrain_job_name" in hcl
        assert "batch_score_job_name" in hcl


# --- Deploy identity gate: no deploy runs under the work account -------------------------------


def test_every_deploy_workflow_gates_on_the_deploy_identity_guard() -> None:
    """The identity guard runs first and every other job descends from it (Golden Rules 2, 7)."""
    for name in ("deploy-backend.yml", "deploy-frontend.yml"):
        jobs = _load_yaml(WORKFLOWS / name)["jobs"]
        identity = jobs["identity"]
        assert "bash scripts/check_deploy_identity.sh" in _job_script(identity)
        assert identity["env"]["AZURE_SUBSCRIPTION_ID"] == "${{ vars.AZURE_SUBSCRIPTION_ID }}"
        assert identity["env"]["AZURE_TENANT_ID"] == "${{ vars.AZURE_TENANT_ID }}"
        # The guard itself must depend on nothing, so it cannot be preceded by a cloud job.
        assert "needs" not in identity
        assert jobs["verify"]["needs"] == "identity"
        # Every remaining job reaches the guard transitively through `verify`.
        for job_name, job in jobs.items():
            if job_name in {"identity", "verify"}:
                continue
            assert job["needs"], f"{name}:{job_name} has no needs edge to the identity gate"
    lifecycle = _deploy_aks()["jobs"]["lifecycle"]
    guard = lifecycle["steps"][1]
    assert "bash scripts/check_deploy_identity.sh" in guard["run"]
    assert guard["env"]["AZURE_SUBSCRIPTION_ID"] == "${{ vars.AZURE_SUBSCRIPTION_ID }}"
    assert guard["env"]["AZURE_TENANT_ID"] == "${{ vars.AZURE_TENANT_ID }}"


def test_deploy_identity_guard_pins_the_personal_repository_and_azure_account() -> None:
    guard = (REPO_ROOT / "scripts" / "check_deploy_identity.sh").read_text()
    assert 'EXPECTED_REPOSITORY="Kartik-Hirijaganer/FraudLens"' in guard
    assert 'EXPECTED_ORIGIN_URL="git@github-personal:Kartik-Hirijaganer/FraudLens.git"' in guard
    assert 'EXPECTED_SUBSCRIPTION_ID="01417138-33d6-4b26-a0e2-38090780d0ec"' in guard
    assert 'EXPECTED_TENANT_ID="057e49df-08cb-4abb-a4ee-469fd4f1954e"' in guard
    # `make pre-pr` must refuse before it formats, regenerates, or runs the CI umbrella.
    makefile = (REPO_ROOT / "Makefile").read_text()
    assert "pre-pr: deploy-identity-check fmt docs ci" in makefile


# --- Deferred AKS workflow: dispatch-only, inert, immutable and recoverable ------------------


def test_aks_workflow_is_dispatch_only_and_feature_gated() -> None:
    text = (WORKFLOWS / "deploy-aks.yml").read_text()
    assert "workflow_dispatch:" in text
    assert "\npush:" not in text and "\npull_request:" not in text
    workflow = _deploy_aks()
    assert list(workflow["jobs"]) == ["lifecycle"]
    lifecycle = workflow["jobs"]["lifecycle"]
    assert "AKS_DEPLOY_ENABLED == 'true'" in lifecycle["if"]
    assert lifecycle["timeout-minutes"] == 240
    assert "AKS_OPERATOR_CIDR" not in text
    assert "api.ipify.org" in text
    assert "AKS_DEPLOY_OBJECT_ID" in text


def test_aks_workflow_builds_one_sha_image_then_smokes_before_evidence() -> None:
    text = (WORKFLOWS / "deploy-aks.yml").read_text()
    flat = _deploy_aks_flat()
    steps = _deploy_aks()["jobs"]["lifecycle"]["steps"]
    names = [step.get("name", "") for step in steps]
    assert text.count("docker/build-push-action") == 1
    assert "fraudlens-backend:${GITHUB_SHA}" in flat
    assert names.index("Run authenticated smoke") < names.index(
        "Capture and validate authenticated HPA and recovery evidence"
    )
    assert names.index("Upload evidence before teardown") < names.index(
        "Always destroy the paid session and verify zero residue"
    )
    assert "actions/upload-artifact" in text
    assert "always() && inputs.action == 'apply-and-verify'" in text
    assert "git commit" not in text and "git push" not in text


def test_aks_workflow_uses_exact_root_kubelogin_and_guarded_destroy() -> None:
    flat = _deploy_aks_flat()
    makefile = (REPO_ROOT / "Makefile").read_text()
    assert "cp $(AKS_DIR)/backend.tf.template $(AKS_DIR)/backend.tf" in makefile
    assert "-var-file=$(AKS_TFVARS)" in makefile
    assert "kubelogin convert-kubeconfig -l azurecli" in flat
    assert "deploy-fraudlens-aks-demo-and-destroy" in flat
    assert "make aks-down CONFIRM=yes" in flat
    assert "make aks-verify-clean" in flat
    assert "terraform -chdir=$(AKS_DIR) state list" in makefile


def test_aks_load_token_is_stdin_only_and_deleted_before_teardown() -> None:
    flat = _deploy_aks_flat()
    assert "--from-file=token=/dev/stdin" in flat
    assert "printf '%s' \"$SMOKE_AUTH_TOKEN\"" in flat
    assert "delete secret fraudlens-load-auth" in flat
    assert 'PORTFOLIO_DEMO_SMOKE_ENABLED: "true"' in (WORKFLOWS / "deploy-aks.yml").read_text()


def test_aks_module_and_tfvars_hold_the_cost_and_security_posture() -> None:
    module = (TERRAFORM / "modules" / "aks" / "main.tf").read_text()
    tfvars = (TERRAFORM / "environments" / "aks-demo" / "aks-demo.tfvars").read_text()
    makefile = (REPO_ROOT / "Makefile").read_text()
    for contract in (
        "sku_tier            = var.sku_tier",
        "local_account_disabled            = true",
        "oidc_issuer_enabled               = true",
        "workload_identity_enabled         = true",
        "run_command_enabled               = false",
        'network_plugin_mode = "overlay"',
        'network_data_plane  = "cilium"',
    ):
        assert contract in module
    assert re.search(r'sku_tier\s*=\s*"Free"', tfvars)
    assert re.search(r"user_min_count\s*=\s*1", tfvars)
    assert re.search(r"user_max_count\s*=\s*2", tfvars)
    assert "wildcard infra/terraform/environments/*/main.tf" in makefile
